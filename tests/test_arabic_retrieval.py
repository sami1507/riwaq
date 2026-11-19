"""Regression: attached Arabic years previously missed literal numeric evidence."""
from authority_fixture import fixture_owner
from backend.knowledge import tokenize,retrieve,init_knowledge
from backend.db import init_db,get_db,now
def test_arabic_attached_years_and_digits_preserve_identifiers():
 for query in ('أقصد2024 وليس2026','في٢٠٢٤ وليس٢٠٢٦'):
  assert {'2024','2026'}<=set(tokenize(query))
 assert 'sha256' in tokenize('sha256') and '256' not in tokenize('sha256')
 assert 'v2' in tokenize('API v2.1') and 'api' in tokenize('API v2.1')
def test_year_correction_retrieves_only_authorized_project(tmp_path,monkeypatch):
 monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));init_db()
 with fixture_owner(), get_db() as c:
  for pid in ('p1','p2'):
   c.execute('INSERT INTO projects VALUES(?,?,?,?)',(pid,pid,'',now()))
   c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',(pid,pid,pid+'.txt',30,pid,1,0,30,now()))
   c.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)',(pid,pid,pid,None,1,1,'Valid year: 2024. Pressure: 17 kPa.' if pid=='p1' else 'Valid year: 2024. PRIVATE_OTHER_PROJECT'))
  hits=retrieve(c,'p1','أقصد2024، وليس2026.')
  assert len(hits)==1 and hits[0]['project_id']=='p1' and '17 kPa' in hits[0]['text']
  assert 'PRIVATE_OTHER_PROJECT' not in str(hits)
