"""CORE-002/003/005 UI audit (python scripts/core_ui_audit.py, service running on 8765): every visible sidebar/top action on the built UI.

Synthetic data and a fake model only. For each action: label, enabled state,
explanation when disabled, what opened, inline errors, console errors and
HTTP >= 400 responses observed while it was open, and whether Escape closes
it and returns focus. Output: $RIWAQ_UI_AUDIT_OUT/ui_audit.json (default reports/core-ui-audit)
"""
import json, os, subprocess, sys, time
from playwright.sync_api import sync_playwright

from pathlib import Path
ROOT = str(Path(__file__).resolve().parents[1])
# Pairing needs the project's own interpreter; the browser driver may live in a separate tools environment.
PY = os.environ.get('RIWAQ_PYTHON') or sys.executable
OUT = os.environ.get('RIWAQ_UI_AUDIT_OUT') or str(Path(ROOT) / 'reports' / 'core-ui-audit')
BASE = os.environ.get('RIWAQ_URL', 'http://127.0.0.1:8765/')
os.makedirs(OUT, exist_ok=True)
if not os.environ.get('RIWAQ_DATA_DIR'):
    raise SystemExit('Refusing to run: set RIWAQ_DATA_DIR to the isolated data folder the service uses.')


def launch(p):
    # Chrome on Linux, Edge on Windows, else Playwright's own Chromium.
    if os.name == 'nt':
        return p.chromium.launch(channel='msedge')
    if os.path.exists('/usr/bin/google-chrome'):
        return p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    return p.chromium.launch()


def code():
    return subprocess.run([PY, '-X', 'utf8', '-B', '-m', 'backend.local_auth', 'pair', '--show'], cwd=ROOT,
                          env={**os.environ}, capture_output=True, text=True).stdout.strip()


def snapshot_dialog(pg):
    return pg.evaluate("""() => {
      const d=[...document.querySelectorAll('[role=dialog],dialog[open],.dialog,.workbench,.studio-panel')].filter(e=>e.offsetParent!==null||getComputedStyle(e).position==='fixed').pop();
      if(!d) return null;
      const h=d.querySelector('h1,h2,h3,[id$=title]');
      const errs=[...d.querySelectorAll('.inline-error,[role=alert]')].map(e=>e.innerText.trim()).filter(Boolean).slice(0,4);
      const notes=[...d.querySelectorAll('.mode-notice,.workspace-safety,[role=note],[role=status]')].map(e=>e.innerText.trim()).filter(Boolean).slice(0,3);
      const btns=[...d.querySelectorAll('button')].filter(b=>b.offsetParent!==null);
      const unnamedEls=btns.filter(b=>!(b.getAttribute('aria-label')||b.getAttribute('aria-labelledby')||b.innerText.trim()||(b.textContent||'').trim()||b.title));const unnamed=unnamedEls.length;
      const disabledUnexplained=btns.filter(b=>b.disabled&&!(b.title||b.getAttribute('aria-describedby')||b.closest('[aria-busy=true]'))).map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim().slice(0,40)).slice(0,8);
      const r=d.getBoundingClientRect();
      return {title:(h?h.innerText:d.getAttribute('aria-label')||'').trim().slice(0,80), text:d.innerText.trim().slice(0,260),
              inline_errors:errs, notices:notes, buttons:btns.length, unnamed_buttons:unnamed, unnamed_html:unnamedEls.map(b=>b.outerHTML.slice(0,160)).slice(0,4),
              disabled_without_explanation:disabledUnexplained,
              overflows_viewport:r.right>window.innerWidth+1||r.left<-1||r.bottom>window.innerHeight+40};
    }""")


def run(viewport_name, vp):
    res = {'viewport': vp, 'actions': []}
    with sync_playwright() as p:
        b = launch(p)
        ctx = b.new_context(viewport=vp, locale='ar')
        pg = ctx.new_page()
        console, failed = [], []
        pg.on('console', lambda m: console.append(m.text[:200]) if m.type == 'error' else None)
        pg.on('response', lambda r: failed.append(f"{r.status} {r.request.method} {r.url.replace(BASE.rstrip('/'), '')[:90]}") if r.status >= 400 else None)
        pg.goto(BASE, wait_until='networkidle')
        pg.locator('input').first.fill(code()); pg.keyboard.press('Enter'); pg.wait_for_timeout(2500)
        res['home_console_errors'] = list(console); res['home_failed'] = list(failed)
        pg.screenshot(path=f'{OUT}/{viewport_name}-home.png')
        # The welcome heading must start below the top bar and inside the viewport (it was scrolled away once).
        res['welcome_heading'] = pg.evaluate("(()=>{const h=document.querySelector('.welcome h1');if(!h)return null;const r=h.getBoundingClientRect();const t=document.querySelector('.topbar,header');const tb=t?t.getBoundingClientRect().bottom:0;return {top:Math.round(r.top),topbar_bottom:Math.round(tb),visible:r.top>=tb-1&&r.bottom<=window.innerHeight}})()")
        narrow = vp['width'] < 900

        def open_sidebar():
            if narrow:
                if pg.evaluate("document.querySelector('.sidebar')?.inert"):
                    pg.locator('.mobile-menu').first.click(); pg.wait_for_timeout(400)

        open_sidebar()
        labels = pg.eval_on_selector_all('.sidebar-action', 'els=>els.map(e=>({label:e.innerText.trim(),disabled:e.disabled,title:e.title||""}))')
        res['sidebar_actions'] = labels
        for idx, item in enumerate(labels):
            entry = dict(item); console.clear(); failed.clear()
            if item['disabled']:
                entry['result'] = 'disabled'
                entry['explained'] = bool(item['title'])
                res['actions'].append(entry); continue
            open_sidebar()
            btn = pg.locator('.sidebar-action').nth(idx)
            btn.click(); pg.wait_for_timeout(1800)
            entry['opened'] = snapshot_dialog(pg)
            pg.screenshot(path=f'{OUT}/{viewport_name}-action-{idx:02d}.png')
            pg.keyboard.press('Escape'); pg.wait_for_timeout(500)
            still = snapshot_dialog(pg)
            if still:
                close = pg.locator('[role=dialog] button[aria-label*="إغلاق"], [role=dialog] button[aria-label*="Close" i]').last
                if close.count():
                    close.click(); pg.wait_for_timeout(400)
            entry['escape_closes'] = still is None
            entry['closed_after'] = snapshot_dialog(pg) is None
            entry['focus_after_close'] = pg.evaluate("(()=>{const e=document.activeElement;return e?(e.className||e.tagName)+':'+(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,30):null})()")
            entry['console_errors'] = list(console); entry['http_errors'] = list(dict.fromkeys(failed))
            res['actions'].append(entry)
        # Settings dialog via top bar (keyboard-only open/close)
        console.clear(); failed.clear()
        s = pg.locator('button[aria-label*="الإعدادات"], button[title*="الإعدادات"]').first
        if s.count():
            s.focus(); pg.keyboard.press('Enter'); pg.wait_for_timeout(1200)
            res['settings'] = snapshot_dialog(pg)
            pg.screenshot(path=f'{OUT}/{viewport_name}-settings.png')
            pg.keyboard.press('Escape'); pg.wait_for_timeout(400)
            res['settings_focus_return'] = pg.evaluate("(()=>{const e=document.activeElement;return e?(e.getAttribute('aria-label')||e.title||e.innerText||e.tagName).slice(0,40):null})()")
            res['settings_http_errors'] = list(dict.fromkeys(failed))
        # Composer reachability with sidebar closed (CORE-003)
        res['composer'] = pg.evaluate("""()=>{const t=document.querySelector('textarea');const send=[...document.querySelectorAll('button')].find(b=>/إرسال|Send/.test(b.getAttribute('aria-label')||b.title||b.innerText));
            const vis=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {top:Math.round(r.top),bottom:Math.round(r.bottom),inside:r.bottom<=innerHeight&&r.top>=0&&r.right<=innerWidth&&r.left>=0}};
            return {textarea:vis(t),send:vis(send),page_overflows_x:document.documentElement.scrollWidth>innerWidth+1}}""")
        ctx.close(); b.close()
    return res


def microphone(granted):
    """CORE-032 with a synthetic audio source (the browser's fake capture device): no real
    microphone is opened and nobody's voice is recorded. granted=False denies the permission.
    Verifies the UI's own state only: tracks opened while held, released afterwards, an honest
    message when denied. It does not measure transcription quality."""
    out = {'granted': granted}
    with sync_playwright() as p:
        flags = ['--use-fake-device-for-media-stream'] + (['--use-fake-ui-for-media-stream'] if granted else ['--deny-permission-prompts'])
        if os.name == 'nt':
            b = p.chromium.launch(channel='msedge', args=flags)
        elif os.path.exists('/usr/bin/google-chrome'):
            b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'] + flags)
        else:
            b = p.chromium.launch(args=flags)
        ctx = b.new_context(viewport={'width': 1366, 'height': 860}, locale='ar', permissions=['microphone'] if granted else [])
        pg = ctx.new_page()
        pg.goto(BASE, wait_until='networkidle')
        pg.locator('input').first.fill(code()); pg.keyboard.press('Enter'); pg.wait_for_timeout(2500)
        entry = pg.locator('.sidebar-action', has_text='جلسة صوت').first
        if not entry.count():
            out['result'] = 'no voice entry in the sidebar'; b.close(); return out
        entry.click(); pg.wait_for_timeout(2500)
        ptt = pg.locator('.voice-ptt').first
        tracks = lambda: pg.evaluate("Number(document.querySelector('.voice-capture')?.dataset.liveTracks||0)")
        alert = lambda: pg.evaluate("[...document.querySelectorAll('.voice-error,[role=alert]')].map(e=>e.innerText.trim()).filter(Boolean).join(' | ').slice(0,200)")
        if not ptt.count() or ptt.is_disabled():
            out['result'] = 'push-to-talk unavailable'; out['alert'] = alert(); b.close(); return out
        box = ptt.bounding_box()
        pg.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        pg.mouse.down(); pg.wait_for_timeout(2500)
        out['while_held'] = {'live_tracks': tracks(), 'recording_class': 'recording' in (ptt.get_attribute('class') or ''), 'alert': alert()}
        pg.mouse.up(); pg.wait_for_timeout(4000)
        out['after_release'] = {'live_tracks': tracks(), 'recording_class': 'recording' in (ptt.get_attribute('class') or ''), 'alert': alert()}
        pg.screenshot(path=f"{OUT}/microphone-{'granted' if granted else 'denied'}.png")
        end = pg.locator('.voice-end').first
        if end.count():
            end.click(); pg.wait_for_timeout(1500)
            out['after_end'] = {'live_tracks': tracks() if pg.locator('.voice-capture').count() else 0}
        b.close()
    out['result'] = 'done'
    return out


if __name__ == '__main__':
    report = {name: run(name, vp) for name, vp in (('desktop', {'width': 1366, 'height': 860}), ('mobile', {'width': 375, 'height': 760}))}
    for key, granted in (('microphone_granted', True), ('microphone_denied', False)):
        try:
            report[key] = microphone(granted)
        except Exception as exc:  # the audit of the workspaces above must still be written
            report[key] = {'granted': granted, 'result': 'error', 'error': str(exc)[:300]}
    json.dump(report, open(f'{OUT}/ui_audit.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for key in ('microphone_granted', 'microphone_denied'):
        print('==', key, report.pop(key))
    for name, r in report.items():
        print('==', name, 'home errors', r['home_console_errors'][:3], r['home_failed'][:5])
        for a in r['actions']:
            o = a.get('opened') or {}
            print(f"  {a['label'][:28]:28s} {a.get('result','opened' if o else 'NOTHING')} title={o.get('title','')[:30]!r} errs={o.get('inline_errors',[])[:2]} esc={a.get('escape_closes')} http={a.get('http_errors',[])[:3]} cons={a.get('console_errors',[])[:2]} undisc={o.get('disabled_without_explanation',[])[:3]} unnamed={o.get('unnamed_buttons')} ovf={o.get('overflows_viewport')}")
        print('  settings', (r.get('settings') or {}).get('title'), 'focus->', r.get('settings_focus_return'), r.get('settings_http_errors'))
        print('  welcome heading', r.get('welcome_heading'))
        print('  composer', r['composer'])
