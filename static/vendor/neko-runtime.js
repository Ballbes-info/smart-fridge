/* Adapted from oneko.js, Copyright (c) 2022 adryd, MIT.
 * https://github.com/adryd325/oneko.js/tree/5281d057c4ea9bd4f6f997ee96ba30491aed16c0
 * Original atlas and directional animation; host controls placement and lifecycle.
 * License: ./oneko.LICENSE.txt. SmartFridge adaptation, 2026.
 */
(() => {
 const clips={idle:[[-3,-3]],alert:[[-7,-3]],scratchSelf:[[-5,0],[-6,0],[-7,0]],tired:[[-3,-2]],sleeping:[[-2,0],[-2,-1]],
  N:[[-1,-2],[-1,-3]],NE:[[0,-2],[0,-3]],E:[[-3,0],[-3,-1]],SE:[[-5,-1],[-5,-2]],S:[[-6,-3],[-7,-2]],SW:[[-5,-3],[-6,-1]],W:[[-4,-2],[-4,-3]],NW:[[-1,0],[-1,-1]]};
 class NekoSprite{
  constructor(element){this.element=element;this.frame=0;this.clip='idle';this.last=0;this.reduced=false;this.draw();}
  set(clip){if(!clips[clip])clip='idle';if(this.clip!==clip){this.clip=clip;this.frame=0;this.last=0;this.draw();}}
  walk(dx,dy){const distance=Math.hypot(dx,dy);if(!distance){this.set('idle');return;}let direction=dy/distance<-.5?'N':dy/distance>.5?'S':'';direction+=dx/distance<-.5?'W':dx/distance>.5?'E':'';this.set(direction||'idle');}
  tick(timestamp){const interval=this.clip==='sleeping'?480:this.clip==='scratchSelf'?170:130;if(timestamp-this.last>=interval){this.last=timestamp;if(!this.reduced)this.frame++;this.draw();}}
  draw(){const clip=clips[this.clip],point=clip[(this.reduced?0:this.frame)%clip.length];this.element.style.backgroundPosition=`${point[0]*64}px ${point[1]*64}px`;this.element.dataset.pose=this.clip;}
  reduce(value){this.reduced=value;this.draw();}
 }
 window.SFNekoSprite=NekoSprite;
})();
