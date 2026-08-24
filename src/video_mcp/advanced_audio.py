from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import json
import os
import shutil
import statistics
import struct
import tempfile
from pathlib import Path
from typing import Any

from .advanced_common import MediaContext, safe_name


def floats(text:str)->list[float]:
    r=[]
    for line in text.splitlines():
        try:r.append(float(line.strip().split()[0]))
        except (ValueError,IndexError):pass
    return r


def register(mcp:Any,c:MediaContext)->None:
    @mcp.tool()
    async def detect_beats(file_id:str)->dict[str,Any]:
        out,_=await c.command(["aubiotrack","-i",str(c.cached(file_id)),"-T","seconds"],c.ffmpeg_timeout);b=floats(out);ints=[y-x for x,y in zip(b,b[1:]) if y>x];return {"file_id":file_id,"beats":b,"tempo_bpm":60/statistics.median(ints) if ints else None}

    @mcp.tool()
    async def detect_tempo(file_id:str)->dict[str,Any]:
        out,_=await c.command(["aubiotrack","-i",str(c.cached(file_id)),"-T","seconds"],c.ffmpeg_timeout);b=floats(out);ints=[y-x for x,y in zip(b,b[1:]) if y>x];return {"file_id":file_id,"tempo_bpm":60/statistics.median(ints) if ints else None,"beat_count":len(b)}

    @mcp.tool()
    async def detect_onsets(file_id:str,method:str="default")->dict[str,Any]:
        a=["aubioonset","-i",str(c.cached(file_id)),"-T","seconds"]+(["-O",method] if method!="default" else []);out,_=await c.command(a,c.ffmpeg_timeout);return {"file_id":file_id,"method":method,"onsets":floats(out)}

    @mcp.tool()
    async def detect_pitch(file_id:str,method:str="default",unit:str="Hz",max_points:int=5000)->dict[str,Any]:
        a=["aubiopitch","-i",str(c.cached(file_id)),"-T","seconds","-u",unit]+(["-p",method] if method!="default" else []);out,_=await c.command(a,c.ffmpeg_timeout);r=[]
        for line in out.splitlines():
            p=line.split()
            if len(p)>=2:
                try:r.append({"time":float(p[0]),"value":float(p[1])})
                except ValueError:pass
            if len(r)>=max(1,min(50000,max_points)):break
        return {"file_id":file_id,"method":method,"unit":unit,"points":r}

    def rn(rawin:Path,rawout:Path)->dict[str,Any]:
        lp=ctypes.util.find_library("rnnoise")
        if not lp:raise RuntimeError("RNNoise library not found")
        lib=ctypes.CDLL(lp);lib.rnnoise_get_frame_size.restype=ctypes.c_int;lib.rnnoise_create.argtypes=[ctypes.c_void_p];lib.rnnoise_create.restype=ctypes.c_void_p;lib.rnnoise_destroy.argtypes=[ctypes.c_void_p];n=int(lib.rnnoise_get_frame_size());arr=ctypes.c_float*n;lib.rnnoise_process_frame.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_float),ctypes.POINTER(ctypes.c_float)];st=lib.rnnoise_create(None)
        if not st:raise RuntimeError("rnnoise_create failed")
        count=0
        try:
            with rawin.open("rb") as f,rawout.open("wb") as o:
                first=True
                while True:
                    b=f.read(n*2)
                    if len(b)<n*2:break
                    vals=struct.unpack("<"+"h"*n,b);inp=arr(*map(float,vals));out=arr();lib.rnnoise_process_frame(st,out,inp)
                    if not first:o.write(struct.pack("<"+"h"*n,*[max(-32768,min(32767,int(round(out[i])))) for i in range(n)]))
                    first=False;count+=1
        finally:lib.rnnoise_destroy(st)
        return {"frames_processed":count,"frame_size":n,"sample_rate":48000}

    @mcp.tool()
    async def denoise_voice(file_id:str,output_filename:str="denoised.wav")->dict[str,Any]:
        src=c.cached(file_id);w=Path(tempfile.mkdtemp(prefix="rn-",dir=c.tmp))
        try:
            a,b=w/"in.pcm",w/"out.pcm";await c.command(["ffmpeg","-y","-i",str(src),"-vn","-ac","1","-ar","48000","-f","s16le",str(a)],c.ffmpeg_timeout);d=await asyncio.to_thread(rn,a,b);oid,out=c.target(safe_name(output_filename,"denoised.wav"));await c.command(["ffmpeg","-y","-f","s16le","-ar","48000","-ac","1","-i",str(b),str(out)],c.ffmpeg_timeout);return c.file_meta(oid,out,"rnnoise.denoise",source_file_id=file_id,**d)
        finally:shutil.rmtree(w,ignore_errors=True)

    silero=Path(os.getenv("SILERO_VAD_MODEL_PATH",str(c.data_root/"models/silero-vad/silero_vad.onnx"))).resolve()
    def vad(raw:Path,threshold:float,min_speech_ms:int,min_silence_ms:int,pad_ms:int)->list[dict[str,float]]:
        import numpy as np,onnxruntime as ort
        if not silero.is_file():raise RuntimeError(f"Silero model not found: {silero}")
        x=np.fromfile(raw,dtype=np.float32);sess=ort.InferenceSession(str(silero),providers=["CPUExecutionProvider"]);sr=16000;win=512;ctx=np.zeros(64,np.float32);state=np.zeros((2,1,128),np.float32);cur=0;tr=False;start=0;temp=None;segments=[];mins=int(sr*max(0,min_speech_ms)/1000);sil=int(sr*max(0,min_silence_ms)/1000)
        for pos in range(0,len(x)-win+1,win):
            chunk=x[pos:pos+win];inp=np.concatenate([ctx,chunk]).reshape(1,-1).astype(np.float32);o=sess.run(["output","stateN"],{"input":inp,"state":state,"sr":np.array([sr],np.int64)});prob=float(np.asarray(o[0]).reshape(-1)[0]);state=np.asarray(o[1],np.float32);cur+=win;ctx=inp.reshape(-1)[-64:].copy()
            if prob>=threshold:
                temp=None
                if not tr:tr=True;start=cur-win
            elif tr and prob<threshold-.15:
                if temp is None:temp=cur
                if cur-temp>=sil:
                    if temp-start>=mins:segments.append((start,temp))
                    tr=False;temp=None
        if tr:segments.append((start,len(x)))
        pad=int(sr*max(0,pad_ms)/1000);merged=[]
        for s,e in segments:
            s,e=max(0,s-pad),min(len(x),e+pad)
            if merged and s<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],e))
            else:merged.append((s,e))
        return [{"start":s/sr,"end":e/sr,"duration":(e-s)/sr} for s,e in merged]

    @mcp.tool()
    async def detect_speech_segments(file_id:str,threshold:float=.5,min_speech_ms:int=250,min_silence_ms:int=100,speech_pad_ms:int=30)->dict[str,Any]:
        src=c.cached(file_id);w=Path(tempfile.mkdtemp(prefix="vad-",dir=c.tmp))
        try:
            raw=w/"a.f32";await c.command(["ffmpeg","-y","-i",str(src),"-vn","-ac","1","-ar","16000","-f","f32le",str(raw)],c.ffmpeg_timeout);r=await asyncio.to_thread(vad,raw,min(1,max(.01,threshold)),min_speech_ms,min_silence_ms,speech_pad_ms);return {"file_id":file_id,"segments":r,"model":silero.name}
        finally:shutil.rmtree(w,ignore_errors=True)

    wb=Path(os.getenv("WHISPER_CPP_BINARY",str(c.data_root/"tooling/whisper.cpp/current/build/bin/whisper-cli"))).resolve();configured_wm=Path(os.getenv("WHISPER_MODEL_PATH",str(c.data_root/"models/whisper/ggml-tiny-q5_1.bin"))).resolve();selected_wm=c.data_root/"models/whisper/selected.bin"
    def whisper_model()->Path:
        try:
            if selected_wm.exists() or selected_wm.is_symlink():
                candidate=selected_wm.resolve()
                if candidate.is_file():return candidate
        except OSError:pass
        return configured_wm
    @mcp.tool()
    async def whisper_info()->dict[str,Any]:
        wm=whisper_model();return {"binary":str(wb),"binary_exists":wb.is_file(),"model":str(wm),"model_exists":wm.is_file(),"selected_override":selected_wm.exists() or selected_wm.is_symlink()}

    async def wav16(src:Path,dst:Path):await c.command(["ffmpeg","-y","-i",str(src),"-vn","-ar","16000","-ac","1","-c:a","pcm_s16le",str(dst)],c.ffmpeg_timeout)

    @mcp.tool()
    async def transcribe(file_id:str,language:str="auto",translate_to_english:bool=False)->dict[str,Any]:
        wm=whisper_model()
        if not wb.is_file() or not wm.is_file():raise RuntimeError("whisper.cpp binary/model is not prepared")
        src=c.cached(file_id);w=Path(tempfile.mkdtemp(prefix="wh-",dir=c.tmp))
        try:
            wav,prefix=w/"in.wav",w/"transcript";await wav16(src,wav);a=[str(wb),"-m",str(wm),"-f",str(wav),"-ojf","-of",str(prefix),"-np"]+(["-l",language] if language!="auto" else [])+(["-tr"] if translate_to_english else []);await c.command(a,c.ffmpeg_timeout);jp=Path(str(prefix)+".json")
            if not jp.is_file():raise RuntimeError("whisper.cpp did not produce JSON")
            data=json.loads(jp.read_text());oid,out=c.target("transcript.json");shutil.copy2(jp,out);return {"transcript":data,"cached_json":c.file_meta(oid,out,"whisper.cpp.transcript",source_file_id=file_id,language=language,translate=translate_to_english)}
        finally:shutil.rmtree(w,ignore_errors=True)

    @mcp.tool()
    async def transcribe_to_subtitles(file_id:str,language:str="auto",output_format:str="srt",output_filename:str="transcript.srt")->dict[str,Any]:
        if output_format not in {"srt","vtt"}:raise ValueError("output_format must be srt or vtt")
        wm=whisper_model()
        if not wb.is_file() or not wm.is_file():raise RuntimeError("whisper.cpp binary/model is not prepared")
        src=c.cached(file_id);w=Path(tempfile.mkdtemp(prefix="whs-",dir=c.tmp))
        try:
            wav,prefix=w/"in.wav",w/"transcript";await wav16(src,wav);a=[str(wb),"-m",str(wm),"-f",str(wav),"-osrt" if output_format=="srt" else "-ovtt","-of",str(prefix),"-np"]+(["-l",language] if language!="auto" else []);await c.command(a,c.ffmpeg_timeout);p=Path(str(prefix)+"."+output_format)
            if not p.is_file():raise RuntimeError("whisper.cpp did not produce subtitles")
            oid,out=c.target(safe_name(output_filename,p.name));shutil.copy2(p,out);return c.file_meta(oid,out,"whisper.cpp.subtitles",source_file_id=file_id,language=language,format=output_format)
        finally:shutil.rmtree(w,ignore_errors=True)

    voices=Path(os.getenv("PIPER_VOICES_ROOT",str(c.data_root/"piper/voices"))).resolve()
    def enabled()->bool:return os.getenv("PIPER_ENABLED","false").lower() in {"1","true","yes","on"}
    @mcp.tool()
    async def piper_info()->dict[str,Any]:return {"enabled":enabled(),"voices_root":str(voices),"voices":[str(p.relative_to(voices)) for p in sorted(voices.rglob("*.onnx"))[:500]] if voices.is_dir() else []}

    @mcp.tool()
    async def piper_import_voice_file(file_id:str,destination:str)->dict[str,Any]:
        src=c.cached(file_id);d=(voices/destination).resolve()
        if not d.is_relative_to(voices) or d.suffix.lower() not in {".onnx",".json"}:raise ValueError("destination must be .onnx/.json below voices root")
        d.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,d);return {"source_file_id":file_id,"destination":str(d.relative_to(voices)),"size_bytes":d.stat().st_size}

    @mcp.tool()
    async def tts_local(text:str,voice_model:str,output_filename:str="tts.wav",length_scale:float=1)->dict[str,Any]:
        if not enabled():raise RuntimeError("Piper is disabled")
        model=(voices/voice_model).resolve()
        if not model.is_relative_to(voices) or model.suffix!=".onnx" or not model.is_file():raise ValueError("Invalid voice model")
        oid,out=c.target(safe_name(output_filename,"tts.wav"))
        def synth():
            import wave
            from piper import PiperVoice,SynthesisConfig
            v=PiperVoice.load(str(model));cfg=SynthesisConfig(length_scale=max(.1,min(5,length_scale)))
            with wave.open(str(out),"wb") as f:v.synthesize_wav(text,f,syn_config=cfg)
        await asyncio.to_thread(synth);return c.file_meta(oid,out,"piper.tts",voice_model=voice_model,length_scale=length_scale)
