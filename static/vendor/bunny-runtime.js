/* Cute Bunny Interactive Character — metamom_mama, CC BY.
 * Original uploaded Rive and attribution: ../pets/bunny/NOTICE.md.
 * Local playback/compositing only; no AI or remote asset requests.
 */
(() => {
 const MACHINE='State Machine 1';
 class BunnySprite{
  constructor(element){
   this.element=element;this.clip='idle';this.direction=1;this.reduced=false;this.loaded=false;this.lastDraw=0;this.background=null;
   this.canvas=document.createElement('canvas');this.canvas.setAttribute('aria-hidden','true');this.canvas.style.width='100%';this.canvas.style.height='100%';element.append(this.canvas);this.ctx=this.canvas.getContext('2d');
   this.source=document.createElement('canvas');this.source.setAttribute('aria-hidden','true');Object.assign(this.source.style,{position:'absolute',inset:'0',width:'100%',height:'100%',opacity:'0',pointerEvents:'none'});element.append(this.source);this.sourceContext=this.source.getContext('2d',{willReadFrequently:true});
   if(!window.rive||!element.dataset.riveSrc){this.fail();return;}
   rive.RuntimeLoader.setWasmUrl(element.dataset.riveWasm);
   this.rive=new rive.Rive({src:element.dataset.riveSrc,canvas:this.source,stateMachine:MACHINE,autoplay:true,enableRiveAssetCDN:false,shouldDisableRiveListeners:true,
    layout:new rive.Layout({fit:rive.Fit.Contain,alignment:rive.Alignment.BottomCenter}),onLoad:()=>{
     this.loaded=true;this.inputs=this.rive.stateMachineInputs(MACHINE)||[];this.hoverInput=this.inputs.find(i=>i.name==='isHovered');this.clickInput=this.inputs.find(i=>i.name==='onClick');
     this.resize();this.applyPose();this.element.dataset.loaded='true';if(this.reduced)this.rive.pause();
    },onLoadError:()=>this.fail()});
   this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(element);
  }
  fail(){this.failed=true;this.element.dataset.assetError='true';this.element.closest('.sf-companion').hidden=true;}
  resize(){if(!this.loaded)return;this.rive.resizeDrawingSurfaceToCanvas(Math.min(devicePixelRatio||1,2));if(this.canvas.width!==this.source.width||this.canvas.height!==this.source.height){this.canvas.width=this.source.width;this.canvas.height=this.source.height;this.background=null;this.dirty=true;}}
  set(clip){clip=({alert:'look',sleeping:'sleep',scratchSelf:'look',tired:'sleep'})[clip]||clip;if(this.clip===clip)return;this.clip=clip;this.element.dataset.pose=clip;this.dirty=true;this.applyPose();}
  walk(dx){if(Math.abs(dx)>.1)this.direction=dx<0?-1:1;this.set('walk');}
  applyPose(){
   if(!this.loaded)return;if(this.hoverInput)this.hoverInput.value=['look','drag'].includes(this.clip);
   if(['drag','jump','land'].includes(this.clip))this.clickInput?.fire();
   if(this.reduced||this.clip==='sleep')this.rive.pause();else this.rive.play(MACHINE);
  }
  tap(){if(this.loaded&&!this.reduced)this.clickInput?.fire();}
  reduce(value){this.reduced=value;this.dirty=true;this.applyPose();}
  /* Remove only the uniform background connected to the image edges. A sealed
   * dark detail (eyes/mouth) is kept; the original .riv is never modified. */
  composite(){
   const w=this.source.width,h=this.source.height;if(!w||!h||!this.sourceContext)return false;
   let image;try{image=this.sourceContext.getImageData(0,0,w,h);}catch(_){this.fail();return false;}
   const pixels=image.data;
   if(!this.background){const i=((h-2)*w+1)*4;if(pixels[i+3]<200)return false;this.background=[pixels[i],pixels[i+1],pixels[i+2]];}
   const total=w*h;if(!this.seen||this.seen.length!==total){this.seen=new Uint8Array(total);this.queue=new Int32Array(total);}else this.seen.fill(0);
   const seen=this.seen,queue=this.queue,bg=this.background;let head=0,tail=0;
   const add=index=>{if(seen[index])return;seen[index]=1;const i=index*4;if(pixels[i+3]<16||(Math.abs(pixels[i]-bg[0])<=8&&Math.abs(pixels[i+1]-bg[1])<=8&&Math.abs(pixels[i+2]-bg[2])<=8)){queue[tail++]=index;pixels[i+3]=0;}};
   for(let x=0;x<w;x++){add(x);add((h-1)*w+x);}for(let y=1;y<h-1;y++){add(y*w);add(y*w+w-1);}
   while(head<tail){const index=queue[head++],x=index%w;if(x)add(index-1);if(x<w-1)add(index+1);if(index>=w)add(index-w);if(index<total-w)add(index+w);}
   this.ctx.clearRect(0,0,w,h);this.ctx.putImageData(image,0,0);
   if(this.clip==='sleep'){this.ctx.fillStyle='#78957b';this.ctx.font=Math.max(9,w*.12)+'px sans-serif';this.ctx.fillText('z',w*.78,h*.38);}
   return true;
  }
  tick(timestamp){
   if(!this.loaded||this.failed)return;
   const bounce=this.clip==='walk'&&!this.reduced?Math.abs(Math.sin(timestamp/145))*3:0;
   this.canvas.style.transform=`translateY(${-bounce}px) scaleX(${this.direction})`;
   if(this.dirty||(!this.reduced&&timestamp-this.lastDraw>=33)){const drawn=this.composite();this.lastDraw=timestamp;this.dirty=!drawn;}
  }
  destroy(){this.observer?.disconnect();this.rive?.cleanup();}
 }
 window.SFBunnySprite=BunnySprite;
})();
