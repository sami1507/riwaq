"""Synthetic SQLite/ZIP regressions; never export the user's actual corpus."""
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile
import pytest
from backend import public_knowledge as public
from backend.knowledge_fetch import FetchResult
from scripts.export_public_knowledge import export_archive


@pytest.fixture
def corpus(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent-registry.json'))
    database=tmp_path/'synthetic.sqlite'
    conn=sqlite3.connect(database)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    public.init_public_knowledge(conn)
    source=public.source_validate({'id':'fixture','name':'Fixture publisher','base_url':'https://docs.example.org/',
        'allowed_hosts':['docs.example.org'],'allowed_prefixes':['/allowed/'],'urls':[],
        'rights_confirmed':True,'status':'allowed','content_type':'text','language':'en',
        'license':{'name':'Synthetic permission','url':'https://docs.example.org/license','storage_allowed':True,
                   'redistribution_allowed':True,'attribution':'Synthetic fixture','reviewed_at':'2026-11-15','evidence':'Test only; no external material'}})
    stamp=public.now()
    conn.execute('INSERT INTO public_sources(id,data,status,created_at,updated_at) VALUES(?,?,?,?,?)',(source['id'],json.dumps(source),'allowed',stamp,stamp))
    conn.executescript("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT); CREATE TABLE messages(id TEXT PRIMARY KEY,content TEXT); INSERT INTO projects VALUES('private-project','PRIVATE_PROJECT_SENTINEL'); INSERT INTO messages VALUES('private-message','PRIVATE_CONVERSATION_SENTINEL');")
    conn.commit()
    yield conn,database,tmp_path,source
    conn.close()


def store(conn,source,url,text,staging=False):
    raw=text.encode('utf-8')
    doc,_=public.store_document(conn,source,{'url':url,'title':'Fixture document'},FetchResult(url,200,{'content-type':'text/plain'},raw),[(1,text)],'SYNTHETIC_LICENSE_EVIDENCE',staging=staging)
    version=conn.execute('SELECT id FROM public_versions WHERE document_id=? ORDER BY revision DESC LIMIT 1',(doc,)).fetchone()[0]
    conn.commit()
    return doc,version


def contents(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read('manifest.json'))
        chunks=[json.loads(line) for line in archive.read('chunks.jsonl').splitlines()]
        joined=b'\n'.join(archive.read(name) for name in archive.namelist())
        return manifest,chunks,archive.namelist(),joined


def test_export_excludes_staged_and_revoked_scope_preserves_ready_metadata(corpus):
    conn,database,folder,source=corpus
    doc,ready=store(conn,source,'https://docs.example.org/allowed/keep/a.txt','READY_PUBLIC_TEXT')
    _,staged=store(conn,source,'https://docs.example.org/allowed/keep/a.txt','STAGED_DO_NOT_EXPORT',staging=True)
    _,withdrawn=store(conn,source,'https://docs.example.org/allowed/withdrawn.txt','REVOKED_SCOPE_DO_NOT_EXPORT')
    source['allowed_prefixes']=['/allowed/keep/']
    conn.execute('UPDATE public_sources SET data=? WHERE id=?',(json.dumps(source),source['id']));conn.commit()
    output=folder/'fixture.zip'
    report=export_archive(database,output)
    manifest,chunks,names,joined=contents(output)
    assert report['versions']==1 and report['documents']==1
    assert report['excluded_counts']=={'version_not_ready':1,'version_outside_current_scope':1}
    assert [row['id'] for row in manifest['versions']]==[ready]
    assert manifest['documents'][0]['current_version_id']==ready
    assert manifest['documents'][0]['current_version_exported'] is True
    index=manifest['index_metadata'][0]
    assert index['version_id']==ready and index['state']=='lexical_ready'
    assert index['source_url']=='https://docs.example.org/allowed/keep/a.txt'
    assert all(row['version_id']==ready for row in chunks)
    assert 'originals/'+ready+'.bin' in names
    assert not any(staged in name or withdrawn in name or '..' in name or name.startswith('/') for name in names)
    for excluded in (b'STAGED_DO_NOT_EXPORT',b'REVOKED_SCOPE_DO_NOT_EXPORT',b'PRIVATE_PROJECT_SENTINEL',b'PRIVATE_CONVERSATION_SENTINEL'):
        assert excluded not in joined
    with zipfile.ZipFile(output) as archive:
        assert hashlib.sha256(archive.read('originals/'+ready+'.bin')).hexdigest()==manifest['versions'][0]['sha256']
    assert conn.execute('SELECT COUNT(*) FROM public_versions').fetchone()[0]==3


def test_each_version_requires_redistribution_and_no_historical_promotion(corpus):
    conn,database,folder,source=corpus
    doc,old=store(conn,source,'https://docs.example.org/allowed/a.txt','PERMITTED_OLD_TEXT')
    restricted=source|{'license':source['license']|{'redistribution_allowed':False}}
    _,current=store(conn,restricted,'https://docs.example.org/allowed/a.txt','NO_REDISTRIBUTION_TEXT')
    output=folder/'fixture.zip';report=export_archive(database,output)
    manifest,chunks,names,joined=contents(output)
    assert report['versions']==1
    assert manifest['documents'][0]['current_version_id'] is None
    assert manifest['documents'][0]['current_version_exported'] is False
    assert [row['id'] for row in manifest['versions']]==[old]
    assert b'NO_REDISTRIBUTION_TEXT' not in joined
    assert conn.execute('SELECT current_version_id FROM public_documents WHERE id=?',(doc,)).fetchone()[0]==current


@pytest.mark.parametrize('change',['suspended','confirmation','storage','redistribution'])
def test_current_source_rights_revoke_export_of_all_versions(corpus,change):
    conn,database,folder,source=corpus
    store(conn,source,'https://docs.example.org/allowed/a.txt','REVOKED_SOURCE_TEXT')
    if change=='suspended':conn.execute("UPDATE public_sources SET status='suspended'")
    else:
        if change=='confirmation':source['rights_confirmed']=False
        elif change=='storage':source['license']['storage_allowed']=False
        else:source['license']['redistribution_allowed']=False
        conn.execute('UPDATE public_sources SET data=?',(json.dumps(source),))
    conn.commit()
    output=folder/'fixture.zip';report=export_archive(database,output)
    manifest,chunks,names,joined=contents(output)
    assert report['versions']==0 and report['sources']==0
    assert manifest['versions']==[] and chunks==[]
    assert b'REVOKED_SOURCE_TEXT' not in joined


def test_failed_hash_does_not_publish_or_replace_existing_archive(corpus):
    conn,database,folder,source=corpus
    store(conn,source,'https://docs.example.org/allowed/a.txt','READY')
    conn.execute("UPDATE public_versions SET original=X'00'");conn.commit()
    output=folder/'fixture.zip';output.write_bytes(b'PRIOR_ARCHIVE_MUST_SURVIVE')
    with pytest.raises(ValueError,match='hash/size'):
        export_archive(database,output)
    assert output.read_bytes()==b'PRIOR_ARCHIVE_MUST_SURVIVE'
    assert list(folder.glob('fixture-*.tmp'))==[]


def test_export_refuses_to_replace_database(corpus):
    _,database,_,_=corpus
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='source database'):
        export_archive(database,database)
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
