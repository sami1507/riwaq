"""QR/Code128 generation and independent decoder, fixed container code only."""
import hashlib,io
from importlib.metadata import version
from backend import isolated_visual_codes as protocol


def generate(raw,options):
    from PIL import Image,ImageDraw
    import segno,barcode,zxingcpp
    text=protocol.text_input(raw,options);scale=options['scale'];kind=options['kind']
    if kind=='qr':
        qr=segno.make_qr(text,error='m',encoding='utf-8',eci=True,boost_error=False)
        matrix=list(qr.matrix_iter(scale=1,border=4));width=len(matrix[0])*scale;height=len(matrix)*scale
        picture=Image.new('RGB',(width,height),'white');draw=ImageDraw.Draw(picture)
        for y,row in enumerate(matrix):
            for x,dark in enumerate(row):
                if dark:draw.rectangle((x*scale,y*scale,(x+1)*scale-1,(y+1)*scale-1),fill='black')
        expected_format=zxingcpp.BarcodeFormat.QRCode;encoder='segno'
    else:
        rows=barcode.Code128(text).build()
        if len(rows)!=1 or set(rows[0])-{'0','1'}:raise ValueError('Invalid Code128 encoding')
        bars=rows[0];width=(len(bars)+20)*scale;height=50*scale
        picture=Image.new('RGB',(width,height),'white');draw=ImageDraw.Draw(picture)
        for x,dark in enumerate(bars):
            if dark=='1':draw.rectangle(((x+10)*scale,10*scale,(x+11)*scale-1,40*scale-1),fill='black')
        expected_format=zxingcpp.BarcodeFormat.Code128;encoder='python-barcode'
    if width*height>4_000_000:raise ValueError('Code pixel limit')
    output=io.BytesIO();picture.save(output,format='PNG');data=output.getvalue()
    if len(data)>protocol.MAX_PNG:raise ValueError('Code output limit')
    # Decode the saved PNG, not the encoder matrix. The reader is a separate library.
    with Image.open(io.BytesIO(data)) as saved:
        decoded=zxingcpp.read_barcodes(saved,formats=expected_format,try_rotate=False,try_downscale=False,try_invert=False)
    if len(decoded)!=1 or not decoded[0].valid or decoded[0].format!=expected_format or decoded[0].bytes!=raw:raise ValueError('Generated code did not decode to exact input')
    source=hashlib.sha256(raw).hexdigest()
    proof={'kind':kind,'scale':scale,'input_sha256':source,'decoded_sha256':hashlib.sha256(decoded[0].bytes).hexdigest(),
      'output_sha256':hashlib.sha256(data).hexdigest(),'width':width,'height':height,'quiet_zone_modules':4 if kind=='qr' else 10,
      'decoder':'zxing-cpp','decoder_version':version('zxing-cpp'),'encoder':encoder,'encoder_version':version(encoder),'decode_matches':True}
    return data,proof
