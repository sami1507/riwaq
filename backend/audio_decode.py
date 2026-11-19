"""Native AV implementation imported only by the fixed isolated audio worker.

Trusted synthetic unit fixtures may call this implementation directly; production
routes must use audio_isolation. Socket guards are not used as the boundary.
"""
from __future__ import annotations
import io
from backend import isolated_audio as protocol

class AudioRejected(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)

def denied(*args,**kwargs):raise AudioRejected('audio_invalid')

def decode(raw):
    import av
    import numpy as np
    try:source_kind=protocol.kind(raw)
    except protocol.AudioProtocolError as exc:raise AudioRejected(str(exc)) from None
    values=[];count=0;frames=0
    try:
        with av.open(io.BytesIO(raw),mode='r',format='wav' if source_kind=='wav' else 'matroska',io_open=denied,
            options={'protocol_whitelist':'','format_whitelist':'wav,matroska,webm','probesize':'1048576','analyzeduration':'5000000'},timeout=(5,5)) as container:
            if len(container.streams.audio)!=1 or len(container.streams)!=1:raise AudioRejected('audio_streams')
            stream=container.streams.audio[0];stream.codec_context.thread_count=2
            channels=len(stream.codec_context.layout.channels);rate=stream.codec_context.sample_rate
            if not 1<=channels<=2 or not 8000<=rate<=192000:raise AudioRejected('audio_rate')
            if container.duration is not None and container.duration/av.time_base>protocol.MAX_SECONDS+.1:raise AudioRejected('audio_duration')
            normalizer=av.AudioResampler(format='flt',layout='mono',rate=protocol.RATE)
            def append(frame):
                nonlocal count
                count+=frame.samples
                if count>protocol.MAX_SAMPLES:raise AudioRejected('audio_duration')
                array=frame.to_ndarray().reshape(-1)
                if array.size!=frame.samples or not np.isfinite(array).all():raise AudioRejected('audio_samples')
                values.append(array)
            for frame in container.decode(audio=0):
                frames+=1
                if frames>30000 or not 8000<=frame.sample_rate<=192000 or not 1<=len(frame.layout.channels)<=2:raise AudioRejected('audio_rate')
                for normalized in normalizer.resample(frame):append(normalized)
            for normalized in normalizer.resample(None):append(normalized)
        if count<protocol.RATE//10:raise AudioRejected('audio_too_short')
        data=np.concatenate(values).astype('<f4',copy=False).tobytes()
        protocol.validate_pcm(data)
        return data,{'kind':source_kind,'sample_rate':rate,'channels':channels},{'av':av.__version__,'numpy':np.__version__}
    except AudioRejected:raise
    except Exception:raise AudioRejected('audio_invalid') from None
