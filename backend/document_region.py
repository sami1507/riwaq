"""Fixed image-region operation for the isolated document worker only.

The HTTP service must never import this module: native image decoding belongs
inside the existing no-network/no-host-mount container, with OS resource limits.
"""
import hashlib
import io
import math
from PIL import Image

def prepare(preview,bbox):
    from backend.isolated_document_page import MAX_REGION_BYTES,MAX_REGION_PIXELS,png_dimensions,validate_region_metadata
    validate_region_metadata({'operation':'region','bbox':bbox})
    width,height=png_dimensions(preview)
    with Image.open(io.BytesIO(preview)) as image:
        if image.format!='PNG' or getattr(image,'n_frames',1)!=1 or image.size!=(width,height):
            raise ValueError('Invalid normalized preview')
        region=bbox or [0.,0.,1.,1.]
        pixels=(math.floor(region[0]*width),math.floor(region[1]*height),math.ceil(region[2]*width),math.ceil(region[3]*height))
        crop=image.crop(pixels).convert('RGB')
        if crop.width*crop.height>MAX_REGION_PIXELS:
            scale=math.sqrt(MAX_REGION_PIXELS/(crop.width*crop.height))
            crop=crop.resize((max(1,int(crop.width*scale)),max(1,int(crop.height*scale))),Image.Resampling.LANCZOS)
        for _ in range(8):
            output=io.BytesIO();crop.save(output,format='PNG');data=output.getvalue()
            if len(data)<=MAX_REGION_BYTES:break
            crop=crop.resize((max(1,int(crop.width*.75)),max(1,int(crop.height*.75))),Image.Resampling.LANCZOS)
        else:raise ValueError('Region size limit')
        return data,{'bbox':[pixels[0]/width,pixels[1]/height,pixels[2]/width,pixels[3]/height],
            'pixel_bbox':list(pixels),'preview_width':width,'preview_height':height,
            'model_image_width':crop.width,'model_image_height':crop.height,
            'region_sha256':hashlib.sha256(data).hexdigest(),'resized':crop.size!=(pixels[2]-pixels[0],pixels[3]-pixels[1])}
