"""Lessons the user teaches Riwaq, kept on this machine and recalled by the local model.

A lesson is text the user wrote or approved: a question with its correct answer, or a piece of
information under a short title. Nothing here calls a network service and nothing is written by a model
on its own. At question time the closest lessons are handed to the model as application data, and the
answer is marked so a recalled lesson is never presented as the model's own knowledge.
"""
from __future__ import annotations

import json
import re

from backend.db import now, uid

MAX_TITLE, MAX_CONTENT, MAX_LESSONS = 400, 4000, 2000
MAX_RECALL, MAX_CONTEXT_CHARS, THRESHOLD = 3, 2400, 0.34
STOP = set('في من على الى إلى عن ما هو هي هل كم كيف لماذا متى اين أين ماذا هذا هذه ذلك التي الذي ان أن إن او أو ثم مع لا لم لن قد كان كانت يكون و ف ب ل the a an of to in on is are was what how why when where which who and or for with do does i my me it'.split())
SCHEMA = '''CREATE TABLE IF NOT EXISTS lessons(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,
enabled INTEGER NOT NULL DEFAULT 1,uses INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS lessons_owner ON lessons(owner_id,enabled);'''


class LessonError(ValueError):
    pass


def init(c):
    c.executescript(SCHEMA)


def norm(text):
    text = re.sub('[ً-ٰٟـ]', '', (text or '').lower())
    text = re.sub('[أإآٱ]', 'ا', text).replace('ى', 'ي').replace('ة', 'ه')
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text)).strip()


def tokens(text):
    out = set()
    for word in norm(text).split():
        if word in STOP or len(word) < 2:
            continue
        if len(word) > 3 and word.startswith('ال'):
            word = word[2:]
        out.add(word)
    return out


def _clean(title, content):
    title, content = (title or '').strip(), (content or '').strip()
    if not title or not content:
        raise LessonError('A lesson needs a title or question and its content.')
    if len(title) > MAX_TITLE or len(content) > MAX_CONTENT:
        raise LessonError('The lesson is too long.')
    if '\x00' in title + content:
        raise LessonError('The lesson contains characters that are not accepted.')
    return title, content


def public(row):
    return {k: row[k] for k in ('id', 'title', 'content', 'enabled', 'uses', 'created_at', 'updated_at')} | {'enabled': bool(row['enabled'])}


def list_lessons(c, owner):
    return [public(r) for r in c.execute('SELECT * FROM lessons WHERE owner_id=? ORDER BY updated_at DESC', (owner,))]


def add(c, owner, title, content):
    title, content = _clean(title, content)
    if c.execute('SELECT COUNT(*) FROM lessons WHERE owner_id=?', (owner,)).fetchone()[0] >= MAX_LESSONS:
        raise LessonError('The lesson notebook is full.')
    stamp, identifier = now(), uid()
    c.execute('INSERT INTO lessons(id,owner_id,title,content,enabled,uses,created_at,updated_at) VALUES(?,?,?,?,1,0,?,?)', (identifier, owner, title, content, stamp, stamp))
    return get(c, owner, identifier)


def get(c, owner, identifier):
    row = c.execute('SELECT * FROM lessons WHERE id=? AND owner_id=?', (identifier, owner)).fetchone()
    if row is None:
        raise KeyError(identifier)
    return public(row)


def update(c, owner, identifier, title=None, content=None, enabled=None):
    current = get(c, owner, identifier)
    title, content = _clean(current['title'] if title is None else title, current['content'] if content is None else content)
    flag = current['enabled'] if enabled is None else bool(enabled)
    c.execute('UPDATE lessons SET title=?,content=?,enabled=?,updated_at=? WHERE id=? AND owner_id=?', (title, content, int(flag), now(), identifier, owner))
    return get(c, owner, identifier)


def delete(c, owner, identifier):
    get(c, owner, identifier)
    c.execute('DELETE FROM lessons WHERE id=? AND owner_id=?', (identifier, owner))


def score(question, title, content):
    """0..1: how well a lesson answers this question. Word overlap only, so it is predictable and local."""
    asked, key = tokens(question), tokens(title)
    if not asked or not key:
        return 0.0
    shared = asked & key
    title_score = len(shared) / len(asked | key)
    body = asked & (key | tokens(content))
    cover = len(body) / len(asked) if len(body) >= 2 else 0.0
    return round(max(title_score, 0.6 * cover), 4)


def recall(c, owner, question):
    """The closest enabled lessons for this question, best first, within the context budget."""
    ranked = []
    for row in c.execute('SELECT id,title,content FROM lessons WHERE owner_id=? AND enabled=1', (owner,)):
        value = score(question, row['title'], row['content'])
        if value >= THRESHOLD:
            ranked.append((value, row['id'], row['title'], row['content']))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    out, used = [], 0
    for value, identifier, title, content in ranked[:MAX_RECALL]:
        if used + len(title) + len(content) > MAX_CONTEXT_CHARS and out:
            break
        out.append({'id': identifier, 'title': title, 'content': content, 'match': value})
        used += len(title) + len(content)
    return out


def mark_used(c, owner, found):
    for item in found:
        c.execute('UPDATE lessons SET uses=uses+1 WHERE id=? AND owner_id=?', (item['id'], owner))


def context(found):
    """Application check data for the request, or {}."""
    if not found:
        return {}
    return {'saved_lessons': {
        'rule': 'These are lessons the user taught this assistant earlier and stored on this device. When a lesson answers the question, answer from it and keep its facts, names and numbers exactly; do not replace them with a guess. If no lesson fits the question, ignore them and answer normally.',
        'lessons': [{'title': f['title'], 'content': f['content']} for f in found]}}


def note(found):
    if not found:
        return ''
    title = found[0]['title'].replace('\n', ' ')
    title = title if len(title) <= 80 else title[:79] + '…'
    more = f' (+{len(found) - 1})' if len(found) > 1 else ''
    return f'⟦من دروسك المحفوظة: «{title}»{more}⟧\n\n'


async def mark_stream(stream, found):
    """Say up front that saved lessons were handed to the model, then pass the stream through untouched."""
    text = note(found)
    if text:
        yield {'content': text}
    async for packet in stream:
        yield packet
