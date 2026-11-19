"""Native image normalization. Imported only by the fixed isolated worker."""
import io
import warnings


class ImageRejected(ValueError):
    def __init__(self, code): self.code=code;super().__init__(code)


def normalize(data,*,variant=None):
    from PIL import Image,ImageOps,UnidentifiedImageError
    from backend.isolated_document_page import MAX_IMAGE_INPUT,MAX_IMAGE_PIXELS,MAX_NORMALIZED_BYTES,MAX_VISUAL_INPUT,MAX_VISUAL_NORMALIZED_BYTES
    if variant not in (None,'visual'):raise ImageRejected('image_invalid')
    input_limit=MAX_VISUAL_INPUT if variant=='visual' else MAX_IMAGE_INPUT
    output_limit=MAX_VISUAL_NORMALIZED_BYTES if variant=='visual' else MAX_NORMALIZED_BYTES
    if not isinstance(data,bytes) or not 1<=len(data)<=input_limit:raise ImageRejected('image_input_limit')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ({'PNG','JPEG','WEBP'} if variant=='visual' else {'PNG','JPEG'}) or getattr(source,'n_frames',1)!=1:raise ImageRejected('image_format')
                if not 0<source.width*source.height<=MAX_IMAGE_PIXELS:raise ImageRejected('image_pixel_limit')
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                picture=ImageOps.exif_transpose(source).convert('RGBA' if variant=='visual' and source.format!='JPEG' else 'RGB')
                picture.info.clear()
                output=io.BytesIO();picture.save(output,format='PNG')
                raw=output.getvalue()
                if len(raw)>output_limit:raise ImageRejected('image_output_limit')
                return raw,{'width':picture.width,'height':picture.height}
    except ImageRejected:raise
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise ImageRejected('image_invalid') from None
