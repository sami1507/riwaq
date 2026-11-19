"""Input/output contracts; synthetic envelopes are not real decoder evidence."""
import base64,hashlib
import pytest
from pydantic import ValidationError
from backend.visual_codes import CodeInput
from backend import isolated_visual_codes as p
from test_document_isolation import png

@pytest.mark.parametrize('kind,text',[('qr','001'),('qr','مرحبا 001'),('qr','Riwaq example'),('code128','001'),('code128','ORDER-ABC')])
def test_literal_text_and_leading_zeros(kind,text):
    body=CodeInput(kind=kind,text=text,request_key='synthetic-key')
    assert p.text_input(body.text.encode(),body.options())==text

@pytest.mark.parametrize('change',[{'text':''},{'text':'   '},{'text':'x\nsecret'},{'text':'a'*513},{'kind':'code128','text':'مرحبا'},{'kind':'code128','text':'a'*81},{'text':1},{'scale':True},{'scale':2},{'scale':7},{'verified':True},{'kind':'shell'}])
def test_bad_code_input_cannot_change_authority_or_budget(change):
    with pytest.raises((ValueError,ValidationError)):CodeInput.model_validate({'kind':'qr','text':'001','request_key':'synthetic-key',**change})

def envelope():
    data=png(12,10);sha=hashlib.sha256(b'001').hexdigest();output=hashlib.sha256(data).hexdigest()
    return {'version':p.VERSION,'success':True,'input_sha256':sha,'proof':{'kind':'qr','scale':4,'input_sha256':sha,'decoded_sha256':sha,'output_sha256':output,'width':12,'height':10,'quiet_zone_modules':4,'decoder':'zxing-cpp','decoder_version':'3.1.1','encoder':'segno','encoder_version':'1.6.6','decode_matches':True},
        'preview':{'bytes':len(data),'sha256':output,'base64':base64.b64encode(data).decode()}}

@pytest.mark.parametrize('change',[lambda x:x['proof'].update(decoded_sha256='f'*64),lambda x:x['proof'].update(decode_matches=False),lambda x:x['proof'].update(width=True),lambda x:x.update(input_sha256='f'*64),lambda x:x['preview'].update(bytes=True),lambda x:x['proof'].update(encoder='model'),lambda x:x.update(success=1),lambda x:x['proof'].update(decoder_version='unreviewed')])
def test_unverified_output_is_not_accepted(change):
    value=envelope();change(value)
    with pytest.raises(ValueError):p.response(p.dump(value),b'001',{'operation':'code','kind':'qr','scale':4})
