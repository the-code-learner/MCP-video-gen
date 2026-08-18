from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import statistics
import tempfile
from pathlib import Path
from typing import Any

from .advanced_common import MediaContext, atempo_chain, safe_name


def register(mcp: Any, c: MediaContext) -> None:
    @mcp.tool()
    async def detect_silence(file_id: str, noise_db: float = -35, min_duration: float = .5) -> dict[str, Any]:
        src=c.cached(file_id); _,e=await c.command(["ffmpeg","-hide_banner","-nostats","-i",str(src),"-af",f"silencedetect=noise={noise_db}dB:d={max(.01,min_duration)}","-f","null","-"],c.ffmpeg_timeout)
        starts=[float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)",e)]
        ends=[(float(a),float(b)) for a,b in re.findall(r"silence_end:\s*([0-9.]+).*?silence_duration:\s*([0-9.]+)",e)]
        return {"file_id":file_id,"intervals":[{"start":s,"end":ends[i][0] if i<len(ends) else None,"duration":ends[i][1] if i<len(ends) else None} for i,s in enumerate(starts)]}

    @mcp.tool()
    async def detect_black_frames(file_id: str, min_duration: float=.2, picture_threshold: float=.98) -> dict[str, Any]:
        src=c.cached(file_id); _,e=await c.command(["ffmpeg","-hide_banner","-nostats","-i",str(src),"-vf",f"blackdetect=d={max(.01,min_duration)}:pic_th={min(1,max(0,picture_threshold))}","-an","-f","null","-"],c.ffmpeg_timeout)
        return {"file_id":file_id,"intervals":[{"start":float(a),"end":float(b),"duration":float(d)} for a,b,d in re.findall(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)",e)]}

    @mcp.tool()
    async def detect_freeze(file_id: str, noise_db: float=-60, min_duration: float=2) -> dict[str, Any]:
        src=c.cached(file_id); _,e=await c.command(["ffmpeg","-hide_banner","-nostats","-i",str(src),"-vf",f"freezedetect=n={noise_db}dB:d={max(.01,min_duration)}","-an","-f","null","-"],c.ffmpeg_timeout)
        s=[float(x) for x in re.findall(r"freeze_start:\s*([0-9.]+)",e)]; en=[float(x) for x in re.findall(r"freeze_end:\s*([0-9.]+)",e)]; d=[float(x) for x in re.findall(r"freeze_duration:\s*([0-9.]+)",e)]
        return {"file_id":file_id,"intervals":[{"start":x,"end":en[i] if i<len(en) else None,"duration":d[i] if i<len(d) else None} for i,x in enumerate(s)]}

    async def loud(src: Path,i:float,tp:float,lra:float)->dict[str,Any]:
        _,e=await c.command(["ffmpeg","-hide_banner","-nostats","-i",str(src),"-af",f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json","-f","null","-"],c.ffmpeg_timeout)
        blocks=re.findall(r"\{\s*\"input_i\".*?\}",e,re.S)
        if not blocks: raise RuntimeError("Could not parse loudnorm analysis")
        return json.loads(blocks[-1])

    @mcp.tool()
    async def analyze_loudness(file_id:str,target_i:float=-16,target_tp:float=-1.5,target_lra:float=11)->dict[str,Any]:
        return await loud(c.cached(file_id),target_i,target_tp,target_lra)

    @mcp.tool()
    async def normalize_loudness(file_id:str,output_filename:str="normalized.wav",target_i:float=-16,target_tp:float=-1.5,target_lra:float=11)->dict[str,Any]:
        src=c.cached(file_id); m=await loud(src,target_i,target_tp,target_lra); oid,out=c.target(safe_name(output_filename,"normalized.wav"))
        f=f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
        await c.command(["ffmpeg","-y","-i",str(src),"-af",f,str(out)],c.ffmpeg_timeout)
        return c.file_meta(oid,out,"ffmpeg.loudnorm",source_file_id=file_id,measurement=m)

    @mcp.tool()
    async def crop_detect(file_id:str,sample_seconds:float=30)->dict[str,Any]:
        src=c.cached(file_id); args=["ffmpeg","-hide_banner","-nostats","-i",str(src)]+(["-t",str(sample_seconds)] if sample_seconds>0 else [])+["-vf","cropdetect=24:2:0","-an","-f","null","-"]
        _,e=await c.command(args,c.ffmpeg_timeout); crops=re.findall(r"crop=(\d+:\d+:\d+:\d+)",e)
        if not crops:return {"file_id":file_id,"crop":None,"samples":0}
        b=max(set(crops),key=crops.count); w,h,x,y=map(int,b.split(":")); return {"file_id":file_id,"crop":{"width":w,"height":h,"x":x,"y":y},"samples":len(crops)}

    @mcp.tool()
    async def detect_interlacing(file_id:str,sample_frames:int=500)->dict[str,Any]:
        src=c.cached(file_id); _,e=await c.command(["ffmpeg","-hide_banner","-nostats","-i",str(src),"-frames:v",str(max(1,sample_frames)),"-filter:v","idet","-an","-f","null","-"],c.ffmpeg_timeout)
        line=next((x for x in reversed(e.splitlines()) if "Multi frame detection:" in x),""); counts={k.lower():int(m.group(1)) if (m:=re.search(rf"{k}:\s*(\d+)",line)) else 0 for k in ("TFF","BFF","Progressive","Undetermined")}
        return {"file_id":file_id,"counts":counts,"dominant":max(counts,key=counts.get)}

    @mcp.tool()
    async def extract_contact_sheet(file_id:str,count:int=12,columns:int=4,width:int=320,output_filename:str="contact-sheet.jpg")->dict[str,Any]:
        src=c.cached(file_id); dur=await c.duration(src); n=max(1,min(100,count)); cols=max(1,min(n,columns)); rows=math.ceil(n/cols); interval=max(dur/n,.01) if dur else 1
        oid,out=c.target(safe_name(output_filename,"contact-sheet.jpg")); vf=f"fps=1/{interval},scale={max(64,width)}:-2,tile={cols}x{rows}:nb_frames={n}"
        await c.command(["ffmpeg","-y","-i",str(src),"-vf",vf,"-frames:v","1",str(out)],c.ffmpeg_timeout); return c.file_meta(oid,out,"ffmpeg.contact_sheet",source_file_id=file_id,count=n)

    @mcp.tool()
    async def compare_ssim_psnr(reference_file_id:str,candidate_file_id:str)->dict[str,Any]:
        a,b=c.cached(reference_file_id),c.cached(candidate_file_id); logs=[]
        for metric in ("ssim","psnr"):
            _,e=await c.command(["ffmpeg","-hide_banner","-i",str(a),"-i",str(b),"-lavfi",f"[1:v][0:v]scale2ref[cmp][ref];[ref][cmp]{metric}","-f","null","-"],c.ffmpeg_timeout); logs.append(e)
        sm=re.findall(r"SSIM .*?All:([0-9.]+)",logs[0]); pm=re.findall(r"PSNR .*?average:([0-9.infINF+-]+)",logs[1]); p=pm[-1] if pm else None
        return {"reference_file_id":reference_file_id,"candidate_file_id":candidate_file_id,"ssim":float(sm[-1]) if sm else None,"psnr_db":math.inf if p and p.lower()=="inf" else (float(p) if p else None)}

    @mcp.tool()
    async def safe_crop(file_id:str,x:int,y:int,width:int,height:int,output_filename:str="cropped.mp4")->dict[str,Any]:
        src=c.cached(file_id); info=await c.probe(src); s=next((z for z in info.get("streams",[]) if z.get("codec_type")=="video"),None)
        if not s: raise ValueError("No video stream")
        sw,sh=int(s.get("width",0)),int(s.get("height",0));
        if sw<2 or sh<2: raise ValueError("Invalid video dimensions")
        x=max(0,min(x,sw-2)); y=max(0,min(y,sh-2)); w=min(max(2,width),sw-x); h=min(max(2,height),sh-y); x-=x%2;y-=y%2;w-=w%2;h-=h%2
        oid,out=c.target(safe_name(output_filename,"cropped.mp4")); await c.command(["ffmpeg","-y","-i",str(src),"-vf",f"crop={w}:{h}:{x}:{y}","-c:v","libx264","-crf","18","-preset","medium","-c:a","copy",str(out)],c.ffmpeg_timeout)
        return c.file_meta(oid,out,"ffmpeg.safe_crop",source_file_id=file_id,crop={"x":x,"y":y,"width":w,"height":h})

    @mcp.tool()
    async def loop_video(file_id:str,loops:int=2,output_filename:str="looped.mp4")->dict[str,Any]:
        src=c.cached(file_id); loops=max(1,min(100,loops)); dur=await c.duration(src); oid,out=c.target(safe_name(output_filename,"looped.mp4")); await c.command(["ffmpeg","-y","-stream_loop",str(loops-1),"-i",str(src),"-t",str(dur*loops),"-c:v","libx264","-crf","18","-c:a","aac",str(out)],c.ffmpeg_timeout); return c.file_meta(oid,out,"ffmpeg.loop",source_file_id=file_id,loops=loops)

    @mcp.tool()
    async def reverse_video(file_id:str,output_filename:str="reversed.mp4")->dict[str,Any]:
        src=c.cached(file_id); oid,out=c.target(safe_name(output_filename,"reversed.mp4")); args=["ffmpeg","-y","-i",str(src),"-vf","reverse"]+(["-af","areverse"] if await c.has_audio(src) else [])+["-c:v","libx264","-crf","18","-c:a","aac",str(out)]; await c.command(args,c.ffmpeg_timeout); return c.file_meta(oid,out,"ffmpeg.reverse",source_file_id=file_id)

    @mcp.tool()
    async def speed_ramp(file_id:str,segments:list[dict[str,float]],output_filename:str="speed-ramp.mp4")->dict[str,Any]:
        if not 1<=len(segments)<=50: raise ValueError("segments must contain 1-50 ranges")
        src=c.cached(file_id); audio=await c.has_audio(src); work=Path(tempfile.mkdtemp(prefix="speed-",dir=c.tmp)); oid,out=c.target(safe_name(output_filename,"speed-ramp.mp4")); parts=[]
        try:
            for i,z in enumerate(segments):
                st,en,sp=max(0,float(z.get("start",0))),float(z.get("end",0)),float(z.get("speed",1));
                if en<=st or sp<=0: raise ValueError("Each segment requires end > start and speed > 0")
                p=work/f"{i:04d}.mp4"; args=["ffmpeg","-y","-ss",str(st),"-to",str(en),"-i",str(src),"-vf",f"setpts=PTS/{sp}","-c:v","libx264","-crf","18"]
                if audio: args += ["-af",atempo_chain(sp),"-c:a","aac","-ar","48000","-ac","2"]
                await c.command(args+[str(p)],c.ffmpeg_timeout); parts.append(p)
            lst=work/"concat.txt"; lst.write_text("".join(f"file '{p.name}'\n" for p in parts)); await c.command(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lst),"-c","copy",str(out)],c.ffmpeg_timeout); return c.file_meta(oid,out,"ffmpeg.speed_ramp",source_file_id=file_id,segments=segments)
        finally: shutil.rmtree(work,ignore_errors=True)

    def cv2mod():
        try: import cv2; return cv2
        except ImportError as e: raise RuntimeError("opencv-python-headless is not installed") from e
    def frame(path:Path,sec:float):
        cv=cv2mod(); cap=cv.VideoCapture(str(path)); cap.set(cv.CAP_PROP_POS_MSEC,max(0,sec)*1000); ok,f=cap.read(); cap.release();
        if not ok or f is None: raise RuntimeError("Could not decode frame")
        return f
    def ahash(f):
        cv=cv2mod(); g=cv.resize(cv.cvtColor(f,cv.COLOR_BGR2GRAY),(8,8)); m=float(g.mean()); v=0
        for bit in (g>=m).flatten(): v=(v<<1)|int(bit)
        return v
    def sim(a:int,b:int)->float:return 1-(a^b).bit_count()/64

    @mcp.tool()
    async def frame_similarity(file_id_a:str,seconds_a:float,file_id_b:str,seconds_b:float)->dict[str,Any]:
        score=await asyncio.to_thread(lambda:sim(ahash(frame(c.cached(file_id_a),seconds_a)),ahash(frame(c.cached(file_id_b),seconds_b)))); return {"similarity":score,"file_id_a":file_id_a,"file_id_b":file_id_b}

    @mcp.tool()
    async def motion_score(file_id:str,sample_fps:float=2,max_samples:int=120)->dict[str,Any]:
        def work():
            cv=cv2mod(); cap=cv.VideoCapture(str(c.cached(file_id))); fps=float(cap.get(cv.CAP_PROP_FPS) or 30); step=max(1,round(fps/max(.1,sample_fps))); prev=None;scores=[];i=0
            while len(scores)<max(1,min(1000,max_samples)):
                ok, f=cap.read()
                if not ok: break
                if i%step==0:
                    g=cv.resize(cv.cvtColor(f,cv.COLOR_BGR2GRAY),(320,180))
                    if prev is not None:scores.append(float(cv.absdiff(g,prev).mean()/255))
                    prev=g
                i+=1
            cap.release(); return {"score":statistics.mean(scores) if scores else 0,"samples":len(scores),"max":max(scores) if scores else 0}
        return {"file_id":file_id,**(await asyncio.to_thread(work))}

    @mcp.tool()
    async def select_best_frame(file_id:str,sample_count:int=24,output_filename:str="best-frame.jpg")->dict[str,Any]:
        def work():
            cv=cv2mod(); cap=cv.VideoCapture(str(c.cached(file_id))); n=int(cap.get(cv.CAP_PROP_FRAME_COUNT) or 0); fps=float(cap.get(cv.CAP_PROP_FPS) or 30); best=None; bs=-1e9;bt=0
            for i in range(max(1,min(200,sample_count))):
                pos=int((n-1)*i/max(1,sample_count-1)) if n>1 else 0; cap.set(cv.CAP_PROP_POS_FRAMES,pos);ok,f=cap.read()
                if not ok:continue
                g=cv.cvtColor(f,cv.COLOR_BGR2GRAY); sharp=float(cv.Laplacian(g,cv.CV_64F).var()); bright=float(g.mean())/255; score=math.log1p(max(0,sharp))+(1-abs(bright-.5)*2)+min(1,float(g.std())/128)
                if score>bs:best,bs,bt=f,score,pos/max(fps,1e-6)
            cap.release()
            if best is None:raise RuntimeError("No decodable frames")
            return best,bt,bs
        f,t,s=await asyncio.to_thread(work); oid,out=c.target(safe_name(output_filename,"best-frame.jpg"))
        if not cv2mod().imwrite(str(out),f):raise RuntimeError("Failed to write frame")
        return c.file_meta(oid,out,"opencv.best_frame",source_file_id=file_id,seconds=t,score=s)

    @mcp.tool()
    async def detect_duplicate_frames(file_id:str,sample_fps:float=4,threshold:float=.98,max_samples:int=2000)->dict[str,Any]:
        def work():
            cv=cv2mod();cap=cv.VideoCapture(str(c.cached(file_id)));fps=float(cap.get(cv.CAP_PROP_FPS) or 30);step=max(1,round(fps/max(.1,sample_fps)));ph=None;pt=0;i=0;n=0;r=[]
            while n<max(1,min(20000,max_samples)):
                ok, f=cap.read()
                if not ok:break
                if i%step==0:
                    h=ahash(f);t=i/fps
                    if ph is not None and (s:=sim(ph,h))>=threshold:r.append({"from":pt,"to":t,"similarity":s})
                    ph,pt=h,t;n+=1
                i+=1
            cap.release();return r
        return {"file_id":file_id,"duplicates":await asyncio.to_thread(work)}

    @mcp.tool()
    async def extract_keyframes(file_id:str,max_frames:int=24,output_prefix:str="keyframe")->dict[str,Any]:
        src=c.cached(file_id); work=Path(tempfile.mkdtemp(prefix="keys-",dir=c.tmp)); out=[]
        try:
            await c.command(["ffmpeg","-y","-skip_frame","nokey","-i",str(src),"-vsync","vfr","-frames:v",str(max(1,min(500,max_frames))),"-q:v","2",str(work/"f-%04d.jpg")],c.ffmpeg_timeout)
            for i,p in enumerate(sorted(work.glob("f-*.jpg")),1): oid,d=c.target(f"{safe_name(output_prefix,'keyframe')}-{i:03d}.jpg");shutil.copy2(p,d);out.append(c.file_meta(oid,d,"ffmpeg.keyframe",source_file_id=file_id,index=i))
            return {"source_file_id":file_id,"outputs":out}
        finally:shutil.rmtree(work,ignore_errors=True)

    @mcp.tool()
    async def make_storyboard(file_id:str,sample_count:int=16,columns:int=4,width:int=360,output_filename:str="storyboard.jpg")->dict[str,Any]:
        src=c.cached(file_id);dur=await c.duration(src);n=max(1,min(64,sample_count));cols=max(1,min(n,columns));rows=math.ceil(n/cols);interval=max(dur/n,.01) if dur else 1;oid,out=c.target(safe_name(output_filename,"storyboard.jpg"));vf=f"fps=1/{interval},scale={max(64,width)}:-2,drawtext=text='%{{pts\\:hms}}':x=10:y=h-th-10:fontsize=24:fontcolor=white:borderw=2:bordercolor=black,tile={cols}x{rows}:nb_frames={n}";await c.command(["ffmpeg","-y","-i",str(src),"-vf",vf,"-frames:v","1",str(out)],c.ffmpeg_timeout);return c.file_meta(oid,out,"ffmpeg.storyboard",source_file_id=file_id,count=n)
