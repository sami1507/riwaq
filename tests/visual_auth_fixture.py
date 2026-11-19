"""Synthetic trusted session setup for component tests; API tests pair normally."""
from contextlib import contextmanager
import time
from backend import local_auth,guard
from backend.db import get_db

@contextmanager
def trusted_visual_session():
    with get_db() as c:
        owner=c.execute('SELECT owner_id FROM local_auth_owner WHERE slot=1').fetchone()[0]
        stamp=time.time();session='synthetic-visual-test-session'
        c.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?,NULL,?)',(session,'synthetic-visual-token-hash',owner,stamp,stamp+3600,stamp))
    token=local_auth._principal.set({'owner_id':owner,'session_id':session})
    try:yield guard.Actor(owner,session)
    finally:local_auth._principal.reset(token)


def synthetic_visual_decoder(monkeypatch):
    """Explicit worker simulation for storage/ACL unit tests, never a live receipt."""
    from backend import document_isolation,image_normalize,isolated_document_page
    def decode(raw,*,authorize=None,variant=None,**_):
        if authorize is None or authorize() is not True:raise document_isolation.IsolationError('document_authority_required',401)
        try:picture,dimensions=image_normalize.normalize(raw,variant=variant)
        except image_normalize.ImageRejected as exc:
            raise document_isolation.IsolationError(exc.code,{'image_input_limit':413,'image_pixel_limit':413,'image_format':415}.get(exc.code,422)) from None
        return picture,dimensions,{'protocol':isolated_document_page.VISUAL_NORMALIZE_VERSION,'boundary':'explicit_unit_simulation','host_fallback':False}
    monkeypatch.setattr(document_isolation,'normalize_image',decode)
