/* Selected Cute Bunny by metamom_mama (CC BY), with original local animation.
 * The uploaded Rive supplies the head and ears. Body, expressions and effects
 * are drawn locally; no model, remote artwork or inference is involved. */
(() => {
 const MACHINE='State Machine 1', clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
 class BunnySprite {
  constructor(element){
   this.element=element;this.clip='idle';this.emotion='content';this.direction=1;this.reduced=false;this.loaded=false;this.lastDraw=0;this.poseAt=performance.now();this.background=null;this.motion={speed:0,vertical:0,impact:0,tilt:0};this.blinkAt=performance.now()+2300;this.blinkUntil=0;this.sparkAt=-10000;
   this.canvas=document.createElement('canvas');this.canvas.setAttribute('aria-hidden','true');Object.assign(this.canvas.style,{width:'100%',height:'100%'});element.append(this.canvas);this.ctx=this.canvas.getContext('2d');
   this.source=document.createElement('canvas');this.source.setAttribute('aria-hidden','true');Object.assign(this.source.style,{position:'absolute',inset:'0',width:'100%',height:'100%',opacity:'0',pointerEvents:'none'});element.append(this.source);this.sourceContext=this.source.getContext('2d',{willReadFrequently:true});
   this.head=document.createElement('canvas');this.headContext=this.head.getContext('2d');
   if(!window.rive||!element.dataset.riveSrc){this.fail();return;}
   rive.RuntimeLoader.setWasmUrl(element.dataset.riveWasm);
   this.rive=new rive.Rive({src:element.dataset.riveSrc,canvas:this.source,stateMachines:MACHINE,autoplay:true,enableRiveAssetCDN:false,shouldDisableRiveListeners:true,
    layout:new rive.Layout({fit:rive.Fit.Contain,alignment:rive.Alignment.BottomCenter}),onLoad:()=>{
     this.loaded=true;this.inputs=this.rive.stateMachineInputs(MACHINE)||[];this.hoverInput=this.inputs.find(i=>i.name==='isHovered');this.clickInput=this.inputs.find(i=>i.name==='onClick');this.resize();this.applyPose();this.element.dataset.loaded='true';
    },onLoadError:()=>this.fail()});
   this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(element);
  }
  fail(){this.failed=true;this.element.dataset.assetError='true';this.element.closest('.sf-companion').hidden=true;}
  resize(){
   if(!this.loaded)return;const dpr=Math.min(devicePixelRatio||1,2);this.rive.resizeDrawingSurfaceToCanvas(dpr);
   if(this.canvas.width!==this.source.width||this.canvas.height!==this.source.height){this.canvas.width=this.head.width=this.source.width;this.canvas.height=this.head.height=this.source.height;this.background=null;this.bounds=null;this.dirty=true;}
  }
  set(clip){clip=({alert:'look',sleeping:'sleep',scratchSelf:'groom',tired:'sleep'})[clip]||clip;if(this.clip===clip)return;this.clip=clip;this.poseAt=performance.now();this.element.dataset.pose=clip;this.dirty=true;this.applyPose();}
  walk(dx){if(Math.abs(dx)>.1)this.direction=dx<0?-1:1;this.set('walk');}
  face(emotion){if(this.emotion===emotion)return;this.emotion=emotion;this.element.dataset.emotion=emotion;this.dirty=true;}
  physics(values){Object.assign(this.motion,values);}
  applyPose(){if(!this.loaded)return;if(this.hoverInput)this.hoverInput.value=['look','drag','peek','wave'].includes(this.clip);if(['drag','jump','land','dance','wave','celebrate'].includes(this.clip))this.clickInput?.fire();if((this.reduced||this.clip==='sleep')&&this.bounds)this.rive.pause();else{this.pauseAfterFrame=this.reduced||this.clip==='sleep';this.rive.play(MACHINE);}}
  tap(){if(this.loaded&&!this.reduced)this.clickInput?.fire();this.sparkAt=performance.now();}
  reduce(value){this.reduced=value;this.dirty=true;this.applyPose();}
  composite(){
   const w=this.source.width,h=this.source.height;if(!w||!h||!this.sourceContext)return false;
   let frame;try{frame=this.sourceContext.getImageData(0,0,w,h);}catch(_){this.fail();return false;}
   const pixels=frame.data;
   if(!this.background){const i=((h-2)*w+1)*4;if(pixels[i+3]<200)return false;this.background=[pixels[i],pixels[i+1],pixels[i+2]];}
   const total=w*h;if(!this.seen||this.seen.length!==total){this.seen=new Uint8Array(total);this.queue=new Int32Array(total);}else this.seen.fill(0);
   const seen=this.seen,queue=this.queue,bg=this.background;let head=0,tail=0;
   const add=index=>{if(seen[index])return;seen[index]=1;const i=index*4;if(pixels[i+3]<16||(Math.abs(pixels[i]-bg[0])<=8&&Math.abs(pixels[i+1]-bg[1])<=8&&Math.abs(pixels[i+2]-bg[2])<=8)){queue[tail++]=index;pixels[i+3]=0;}};
   for(let x=0;x<w;x++){add(x);add((h-1)*w+x);}for(let y=1;y<h-1;y++){add(y*w);add(y*w+w-1);}
   while(head<tail){const index=queue[head++],x=index%w;if(x)add(index-1);if(x<w-1)add(index+1);if(index>=w)add(index-w);if(index<total-w)add(index+w);}
   this.headContext.putImageData(frame,0,0);
   if(!this.bounds){let l=w,r=0,t=h,b=0;for(let y=0;y<h;y++)for(let x=0;x<w;x++)if(pixels[(y*w+x)*4+3]>100){l=Math.min(l,x);r=Math.max(r,x);t=Math.min(t,y);b=Math.max(b,y);}if(r-l<10)return false;this.bounds={x:Math.max(0,l-2),y:Math.max(0,t-2),w:Math.min(w-l,r-l+5),h:Math.min(h-t,b-t+5)};const i=(Math.round(t+(b-t)*.58)*w+Math.round((l+r)/2))*4;this.fur=pixels[i+3]>200?`rgb(${pixels[i]},${pixels[i+1]},${pixels[i+2]})`:'#ffe0b3';}
   return true;
  }
  ellipse(c,x,y,rx,ry,fill,stroke){c.beginPath();c.ellipse(x,y,rx,ry,0,0,Math.PI*2);if(fill){c.fillStyle=fill;c.fill();}if(stroke){c.strokeStyle=stroke;c.stroke();}}
  features(c,x,y,w,h,emotion,blink,time){
   const X=n=>x+n*w,Y=n=>y+n*h,ink='#4c3a32';c.save();c.lineCap='round';c.lineJoin='round';c.lineWidth=1.35;
   this.ellipse(c,X(.5),Y(.735),w*.35,h*.225,this.fur);
   this.ellipse(c,X(.23),Y(.77),w*.075,h*.046,'#eea892');this.ellipse(c,X(.77),Y(.77),w*.075,h*.046,'#eea892');
   c.strokeStyle=ink;c.fillStyle=ink;
   for(const side of [-1,1]){const ex=X(.5+side*.17),ey=Y(.684);
    if(blink||['happy','excited','proud','sleepy'].includes(emotion)){c.beginPath();c.moveTo(ex-3.5,ey);c.quadraticCurveTo(ex,ey+(['sleepy'].includes(emotion)?2:-5),ex+3.5,ey);c.stroke();}
    else if(['surprised','worried'].includes(emotion)){this.ellipse(c,ex,ey,emotion==='surprised'?3.7:2.6,4.8,'#fff7e8',ink);this.ellipse(c,ex,ey+.3,1.8,2.9,ink);}
    else if(emotion==='dizzy'){c.beginPath();for(let i=0;i<24;i++){const a=i*.5,r=i*.14,xx=ex+Math.cos(a)*r,yy=ey+Math.sin(a)*r;i?c.lineTo(xx,yy):c.moveTo(xx,yy);}c.stroke();}
    else {this.ellipse(c,ex+Math.sin(time*.0006)*.5,ey,2.6,3.5,ink);this.ellipse(c,ex-.7,ey-1,0.75,1,'#fff7e8');}
    if(['surprised','curious','worried'].includes(emotion)){c.beginPath();const tilt=emotion==='curious'?side*1.4:0;c.moveTo(ex-3.5,Y(.605)+tilt);c.quadraticCurveTo(ex,Y(.58)-tilt,ex+3.5,Y(.605)-tilt);c.stroke();}
   }
   c.fillStyle='#bf8174';c.beginPath();c.moveTo(X(.46),Y(.775));c.quadraticCurveTo(X(.5),Y(.755),X(.54),Y(.775));c.lineTo(X(.5),Y(.81));c.closePath();c.fill();c.strokeStyle=ink;
   if(['surprised','worried'].includes(emotion))this.ellipse(c,X(.5),Y(.86),2.2,3.2,'#795047');
   else if(emotion==='excited'){this.ellipse(c,X(.5),Y(.86),4.5,4,'#795047');this.ellipse(c,X(.5),Y(.88),2.6,1.6,'#e5a093');}
   else if(emotion==='sleepy'){c.beginPath();c.moveTo(X(.46),Y(.85));c.lineTo(X(.54),Y(.85));c.stroke();}
   else {c.beginPath();c.moveTo(X(.5),Y(.81));c.lineTo(X(.5),Y(.84));c.quadraticCurveTo(X(.46),Y(.9),X(.43),Y(.85));c.moveTo(X(.5),Y(.84));c.quadraticCurveTo(X(.54),Y(.9),X(.57),Y(.85));c.stroke();}
   c.restore();
  }
  draw(time){
   const c=this.ctx,W=this.canvas.width,H=this.canvas.height;if(!this.bounds||!W||!H)return;c.setTransform(1,0,0,1,0,0);c.clearRect(0,0,W,H);c.scale(W/80,H/112);
   const age=(time-this.poseAt)/1000,phase=time/120,walk=this.clip==='walk',dance=['dance','celebrate'].includes(this.clip),sleep=this.clip==='sleep',drag=this.clip==='drag',fall=this.clip==='fall',jump=this.clip==='jump',land=this.clip==='land';
   const moving=!this.reduced,hop=walk&&moving?Math.abs(Math.sin(phase))*2.8:0,sway=dance&&moving?Math.sin(age*12)*.065:drag?clamp(this.motion.tilt||0,-.065,.065):this.clip==='curious'?.06:0;
   let sx=1,sy=1;if(fall||drag){sx=.92;sy=1.06;}if(land&&moving){const bounce=Math.sin(Math.min(1,age/.55)*Math.PI)*Math.max(.10,this.motion.impact*.24);sx=1+bounce;sy=1-bounce;}if(this.clip==='stretch'&&moving){sy=1+Math.sin(age*2)*.07;sx=1/Math.sqrt(sy);}
   const breath=moving?Math.sin(time/950)*.55:0,bodyY=sleep?91:84;
   c.save();c.translate(40,108);c.rotate(sway);c.scale(sx*this.direction,sy);c.translate(-40,-108-hop);
   c.lineWidth=1.1;const outline='#b8a38a',fur=this.fur||'#ffe0b3';
   this.ellipse(c,59,bodyY+7,7,6,'#fff0d8',outline);
   // Hind feet alternate with the walking cycle; arms have their own shoulder joints.
   for(const side of [-1,1]){let footY=105+(walk&&moving?Math.sin(phase+side*1.4)*1.7:0);this.ellipse(c,40+side*12,footY,10,4.6,fur,outline);this.ellipse(c,40+side*12,footY+.4,4.8,1.7,'#efbcaa');}
   this.ellipse(c,40,bodyY,19,sleep?14:22,fur,outline);this.ellipse(c,40,bodyY+3,11,sleep?9:15,'#fff1d9');
   for(const side of [-1,1]){const raised=dance||this.clip==='wave'&&side===1||drag||fall||jump||this.clip==='stretch';let angle=raised?side*(-1.1+Math.sin(age*10)*.15):side*.16+(walk&&moving?Math.sin(phase+side)*.4:0);if(this.clip==='groom'&&side===1)angle=-1.5+Math.sin(age*13)*.2;c.save();c.translate(40+side*16,bodyY-9);c.rotate(angle);this.ellipse(c,0,8,4.7,9,fur,outline);this.ellipse(c,0,13,2.3,2.6,'#edb7a6');c.restore();}
   // Green neckerchief ties the character into the app's palette.
   c.fillStyle='#3b6656';c.beginPath();c.moveTo(25,68);c.quadraticCurveTo(40,77,55,68);c.lineTo(44,84);c.lineTo(38,78);c.lineTo(33,85);c.closePath();c.fill();
   for(const [x,y]of[[38.5,77],[41.5,77],[40,79]])this.ellipse(c,x,y,1.5,1.7,'#bbd3a4');
   const hw=66,hh=70,hx=7,hy=5+(sleep?10:0)+breath+(this.clip==='peek'?Math.sin(age*3)*2:0),headTilt=this.clip==='look'||this.emotion==='curious'?.08:drag?-sway*.6:0;
   c.save();c.translate(40,hy+60);c.rotate(headTilt);c.translate(-40,-(hy+60));const b=this.bounds;c.drawImage(this.head,b.x,b.y,b.w,b.h,hx,hy,hw,hh);
   if(time>=this.blinkAt&&moving){this.blinkUntil=time+130;this.blinkAt=time+2800+Math.random()*3500;}
   this.features(c,hx,hy,hw,hh,sleep?'sleepy':this.emotion,time<this.blinkUntil,time);c.restore();c.restore();
   if(sleep){c.fillStyle='#78957b';c.font='bold 11px sans-serif';c.fillText('z',65,30-(moving?age%2*3:0));c.font='bold 8px sans-serif';c.fillText('z',72,21);}
   const sparkle=(time-this.sparkAt)/1000;if(moving&&sparkle>=0&&sparkle<1.2){c.save();c.globalAlpha=1-sparkle/1.2;for(let i=0;i<5;i++){const angle=i*Math.PI*2/5,s=3*(1-sparkle/1.5),x=40+Math.cos(angle)*(22+sparkle*15),y=48+Math.sin(angle)*(22+sparkle*15)-sparkle*8;c.fillStyle=['#e9b96d','#d58f8f','#91b7a0'][i%3];c.beginPath();for(let k=0;k<8;k++){const a=k*Math.PI/4,r=k%2?s*.4:s;k?c.lineTo(x+Math.cos(a)*r,y+Math.sin(a)*r):c.moveTo(x+r,y);}c.closePath();c.fill();}c.restore();}
   if(land&&moving&&age<.6){c.save();c.globalAlpha=(1-age/.6)*.45;c.fillStyle='#b7a991';for(const side of [-1,1])this.ellipse(c,40+side*(18+age*26),108-age*6,3+age*4,1.5+age*2,'#b7a991');c.restore();}
  }
  tick(timestamp){if(!this.loaded||this.failed)return;if(this.dirty||(!this.reduced&&timestamp-this.lastDraw>=33)){const ready=this.composite();if(ready){this.draw(timestamp);if(this.pauseAfterFrame){this.rive.pause();this.pauseAfterFrame=false;}}this.lastDraw=timestamp;this.dirty=!ready;}}
  destroy(){this.observer?.disconnect();this.rive?.cleanup();}
 }
 window.SFBunnySprite=BunnySprite;
})();
