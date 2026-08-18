from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from .advanced_common import MediaContext, safe_name

_TIMELINE_ID=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def register(mcp:Any,c:MediaContext)->None:
    def scenes_sync(path:Path,detector:str,threshold:float,min_seconds:float)->list[dict[str,Any]]:
        try: from scenedetect import AdaptiveDetector,ContentDetector,SceneManager,open_video
        except ImportError as e: raise RuntimeError("PySceneDetect is not installed") from e
        v=open_video(str(path));fps=float(v.frame_rate or 30);sm=SceneManager();ml=max(1,round(min_seconds*fps))
        if detector=="adaptive": sm.add_detector(AdaptiveDetector(adaptive_threshold=max(.1,3.0 if threshold==27 else threshold),min_scene_len=ml))
        elif detector=="content": sm.add_detector(ContentDetector(threshold=max(.1,threshold),min_scene_len=ml))
        else: raise ValueError("detector must be content or adaptive")
        sm.detect_scenes(video=v,show_progress=False);r=[]
        for i,(a,b) in enumerate(sm.get_scene_list(start_in_scene=True),1):
            s,e=float(a.get_seconds()),float(b.get_seconds());r.append({"index":i,"start":s,"end":e,"duration":max(0,e-s),"start_frame":int(a.get_frames()),"end_frame":int(b.get_frames())})
        return r

    @mcp.tool()
    async def detect_scenes(file_id:str,detector:str="content",threshold:float=27,min_scene_seconds:float=.6)->dict[str,Any]:
        return {"file_id":file_id,"detector":detector,"scenes":await asyncio.to_thread(scenes_sync,c.cached(file_id),detector,threshold,min_scene_seconds)}

    @mcp.tool()
    async def split_scenes(file_id:str,detector:str="content",threshold:float=27,min_scene_seconds:float=.6,max_scenes:int=100)->dict[str,Any]:
        src=c.cached(file_id);sc=await asyncio.to_thread(scenes_sync,src,detector,threshold,min_scene_seconds);outs=[]
        for z in sc[:max(1,min(100,max_scenes))]:
            oid,out=c.target(f"scene-{z['index']:03d}.mp4");await c.command(["ffmpeg","-y","-ss",str(z["start"]),"-t",str(z["duration"]),"-i",str(src),"-c:v","libx264","-crf","18","-preset","medium","-c:a","aac",str(out)],c.ffmpeg_timeout);outs.append(c.file_meta(oid,out,"scenedetect.split",source_file_id=file_id,scene=z))
        return {"source_file_id":file_id,"outputs":outs}

    @mcp.tool()
    async def scene_thumbnails(file_id:str,detector:str="content",threshold:float=27,min_scene_seconds:float=.6,max_scenes:int=100)->dict[str,Any]:
        src=c.cached(file_id);sc=await asyncio.to_thread(scenes_sync,src,detector,threshold,min_scene_seconds);outs=[]
        for z in sc[:max(1,min(100,max_scenes))]:
            at=z["start"]+z["duration"]/2;oid,out=c.target(f"scene-{z['index']:03d}.jpg");await c.command(["ffmpeg","-y","-ss",str(at),"-i",str(src),"-frames:v","1","-q:v","2",str(out)],c.ffmpeg_timeout);outs.append(c.file_meta(oid,out,"scenedetect.thumbnail",source_file_id=file_id,scene=z,seconds=at))
        return {"source_file_id":file_id,"outputs":outs}

    @mcp.tool()
    async def subtitle_create(cues:list[dict[str,Any]],output_filename:str="subtitles.srt",format:str="srt")->dict[str,Any]:
        import pysubs2
        if not 1<=len(cues)<=10000:raise ValueError("cues must contain 1-10000 entries")
        s=pysubs2.SSAFile()
        for q in cues:
            a,b=int(float(q.get("start",0))*1000),int(float(q.get("end",0))*1000)
            if b<=a:raise ValueError("cue end must be greater than start")
            s.events.append(pysubs2.SSAEvent(start=a,end=b,text=str(q.get("text",""))))
        oid,out=c.target(safe_name(output_filename,f"subtitles.{format}"));s.save(str(out),format_=format);return c.file_meta(oid,out,"pysubs2.create",cues=len(cues),format=format)

    @mcp.tool()
    async def subtitle_shift(file_id:str,milliseconds:int,output_filename:str="shifted.srt")->dict[str,Any]:
        import pysubs2
        s=pysubs2.load(str(c.cached(file_id)));s.shift(ms=milliseconds);oid,out=c.target(safe_name(output_filename,"shifted.srt"));s.save(str(out));return c.file_meta(oid,out,"pysubs2.shift",source_file_id=file_id,milliseconds=milliseconds)

    @mcp.tool()
    async def subtitle_convert(file_id:str,output_format:str="srt",output_filename:str="converted.srt")->dict[str,Any]:
        import pysubs2
        s=pysubs2.load(str(c.cached(file_id)));oid,out=c.target(safe_name(output_filename,f"converted.{output_format}"));s.save(str(out),format_=output_format);return c.file_meta(oid,out,"pysubs2.convert",source_file_id=file_id,format=output_format)

    @mcp.tool()
    async def subtitle_style_ass(file_id:str,output_filename:str="styled.ass",font_name:str="DejaVu Sans",font_size:float=48,bold:bool=False,alignment:int=2,margin_v:int=40)->dict[str,Any]:
        import pysubs2
        s=pysubs2.load(str(c.cached(file_id)));st=s.styles.get("Default",pysubs2.SSAStyle());st.fontname=font_name;st.fontsize=max(6,min(300,font_size));st.bold=bold;st.alignment=max(1,min(9,alignment));st.marginv=max(0,margin_v);s.styles["Default"]=st
        for e in s.events:e.style="Default"
        oid,out=c.target(safe_name(output_filename,"styled.ass"));s.save(str(out),format_="ass");return c.file_meta(oid,out,"pysubs2.style",source_file_id=file_id)

    @mcp.tool()
    async def subtitle_burn(video_file_id:str,subtitle_file_id:str,output_filename:str="subtitled.mp4")->dict[str,Any]:
        v,s=c.cached(video_file_id),c.cached(subtitle_file_id);oid,out=c.target(safe_name(output_filename,"subtitled.mp4"));esc=str(s).replace("\\","\\\\").replace(":","\\:").replace("'","\\'");await c.command(["ffmpeg","-y","-i",str(v),"-vf",f"subtitles='{esc}'","-c:v","libx264","-crf","18","-preset","medium","-c:a","copy",str(out)],c.ffmpeg_timeout);return c.file_meta(oid,out,"ffmpeg.subtitle_burn",video_file_id=video_file_id,subtitle_file_id=subtitle_file_id)

    root=(c.data_root/"timelines").resolve()
    def tpath(tid:str)->Path:
        if not _TIMELINE_ID.fullmatch(tid):raise ValueError("Invalid timeline_id")
        return root/f"{tid}.otio"
    def load(tid:str):
        import opentimelineio as otio
        p=tpath(tid)
        if not p.is_file():raise ValueError("Timeline not found")
        return otio.adapters.read_from_file(str(p))
    def save(tid:str,t):
        import opentimelineio as otio
        root.mkdir(parents=True,exist_ok=True);otio.adapters.write_to_file(t,str(tpath(tid)))
    def track(t,name:str,create:bool=False,kind:str="video"):
        import opentimelineio as otio
        for x in t.tracks:
            if x.name==name:return x
        if not create:raise ValueError("Track not found")
        x=otio.schema.Track(name=name,kind=otio.schema.TrackKind.Audio if kind.lower()=="audio" else otio.schema.TrackKind.Video);t.tracks.append(x);return x

    @mcp.tool()
    async def timeline_create(timeline_id:str,fps:float=30)->dict[str,Any]:
        import opentimelineio as otio
        if tpath(timeline_id).exists():raise ValueError("Timeline already exists")
        t=otio.schema.Timeline(name=timeline_id);t.metadata["video_mcp_fps"]=max(1,min(240,fps));save(timeline_id,t);return {"timeline_id":timeline_id,"fps":t.metadata["video_mcp_fps"]}

    @mcp.tool()
    async def timeline_inspect(timeline_id:str)->dict[str,Any]:
        t=load(timeline_id);tracks=[]
        for tr in t.tracks:
            items=[]
            for x in tr:
                z={"schema":x.schema_name(),"name":getattr(x,"name","")}
                if getattr(x,"source_range",None):z|={"source_start_seconds":x.source_range.start_time.to_seconds(),"source_duration_seconds":x.source_range.duration.to_seconds()}
                items.append(z)
            tracks.append({"name":tr.name,"kind":tr.kind,"items":items})
        return {"timeline_id":timeline_id,"name":t.name,"metadata":dict(t.metadata),"tracks":tracks}

    @mcp.tool()
    async def timeline_add_clip(timeline_id:str,file_id:str,track_name:str="V1",name:str="",source_start:float=0,duration_seconds:float=0,kind:str="video")->dict[str,Any]:
        import opentimelineio as otio
        t=load(timeline_id);tr=track(t,track_name,True,kind);src=c.cached(file_id);fps=float(t.metadata.get("video_mcp_fps",30));md=await c.duration(src);d=duration_seconds if duration_seconds>0 else max(0,md-source_start)
        if d<=0:raise ValueError("Could not determine clip duration")
        ref=otio.schema.ExternalReference(target_url=src.as_uri(),available_range=otio.opentime.TimeRange(otio.opentime.RationalTime(0,fps),otio.opentime.RationalTime(md*fps,fps)));clip=otio.schema.Clip(name=name or src.name.split("__",1)[-1],media_reference=ref,source_range=otio.opentime.TimeRange(otio.opentime.RationalTime(source_start*fps,fps),otio.opentime.RationalTime(d*fps,fps)));clip.metadata["video_mcp_file_id"]=file_id;tr.append(clip);save(timeline_id,t);return {"timeline_id":timeline_id,"track":track_name,"index":len(tr)-1,"file_id":file_id}

    @mcp.tool()
    async def timeline_move_clip(timeline_id:str,track_name:str,from_index:int,to_index:int)->dict[str,Any]:
        t=load(timeline_id);tr=track(t,track_name)
        if not 0<=from_index<len(tr):raise ValueError("from_index out of range")
        x=tr.pop(from_index);to_index=max(0,min(to_index,len(tr)));tr.insert(to_index,x);save(timeline_id,t);return {"timeline_id":timeline_id,"track":track_name,"to_index":to_index}

    @mcp.tool()
    async def timeline_add_transition(timeline_id:str,track_name:str,after_index:int,duration_seconds:float=.5,transition_type:str="SMPTE_Dissolve")->dict[str,Any]:
        import opentimelineio as otio
        t=load(timeline_id);tr=track(t,track_name)
        if not 0<=after_index<len(tr):raise ValueError("after_index out of range")
        fps=float(t.metadata.get("video_mcp_fps",30));half=otio.opentime.RationalTime(max(0,duration_seconds)*fps/2,fps);tr.insert(after_index+1,otio.schema.Transition(name=transition_type,transition_type=transition_type,in_offset=half,out_offset=half));save(timeline_id,t);return {"timeline_id":timeline_id,"index":after_index+1}

    @mcp.tool()
    async def timeline_add_marker(timeline_id:str,track_name:str,name:str,seconds:float,color:str="RED")->dict[str,Any]:
        import opentimelineio as otio
        t=load(timeline_id);tr=track(t,track_name);fps=float(t.metadata.get("video_mcp_fps",30));col=getattr(otio.schema.MarkerColor,color.upper(),otio.schema.MarkerColor.RED);tr.markers.append(otio.schema.Marker(name=name,marked_range=otio.opentime.TimeRange(otio.opentime.RationalTime(max(0,seconds)*fps,fps),otio.opentime.RationalTime(0,fps)),color=col));save(timeline_id,t);return {"timeline_id":timeline_id,"marker":name,"seconds":seconds}

    @mcp.tool()
    async def timeline_export(timeline_id:str,output_filename:str="timeline.otio")->dict[str,Any]:
        src=tpath(timeline_id)
        if not src.is_file():raise ValueError("Timeline not found")
        oid,out=c.target(safe_name(output_filename,"timeline.otio"));shutil.copy2(src,out);return c.file_meta(oid,out,"opentimelineio.export",timeline_id=timeline_id)
