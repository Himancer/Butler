import assert from 'node:assert/strict';
import {test} from 'node:test';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import * as ui from '../static/ui.mjs';
import * as studio from '../static/studio.mjs';

function harness() {
  const nodes=new Map(), stored=new Map();
  const node=id=>{
    if(!nodes.has(id)) nodes.set(id,{id,value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,checked:false,open:false,readyState:0,currentTime:0,duration:12,
      selectedOptions:[{textContent:'Default'}],dataset:{},classList:{toggle(){},add(){},remove(){}},attrs:{},
      getAttribute(k){return this.attrs[k]||null;},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
      pause(){},load(){},close(){this.open=false;},showModal(){this.open=true;},addEventListener(){},click(){},remove(){}});
    return nodes.get(id);
  };
  const ctx=vm.createContext({...ui,...studio,URL:{revokeObjectURL(){},createObjectURL(){return 'blob:test';}},URLSearchParams,
    document:{getElementById:node,querySelectorAll:()=>[],querySelector:()=>({value:'letterbox'}),addEventListener(){},body:{append(){}}},
    window:{addEventListener(){}},sessionStorage:{setItem:(k,v)=>stored.set(k,v),getItem:k=>stored.get(k)||null},
    setTimeout:()=>1,clearTimeout(){},crypto:{randomUUID:()=> 'key'},location:{hash:''},history:{replaceState(){}},productArt:()=>'',
    fetch:async()=>{throw new Error('Unexpected request');}});
  const source=readFileSync(new URL('../static/app.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'').replace(/\ninitialize\(\);\s*$/,'');
  vm.runInContext(source+'\nglobalThis.app={state,selectStudio,pollWork,generateCaption,collectEdit,renderCreate,refreshWorkspace,reviewPlan,downloadArtifact};',ctx);
  const state=ctx.app.state;
  state.session={id:'session',csrf_token:'token',preferences:{tone:'friendly'},selected_product_ids:[]};
  state.capabilities={model_mode:'local',media:{ready:true,ffprobe:true,ffmpeg:true},max_upload_bytes:30000000};
  return {ctx,node,state,app:ctx.app,stored};
}
const a={id:'a',validation_status:'valid',duration_seconds:12,original_filename:'a.mp4'};
const b={id:'b',validation_status:'valid',duration_seconds:5,original_filename:'b.mp4'};

test('removing a clip clears the draft, copy, approvals, output and remembers the removal',()=>{
  const h=harness();h.state.assets=[a];h.state.plans=[{id:'p',asset_id:'a'}];h.state.jobs=[{id:'j',plan_id:'p',status:'succeeded',output_keys:[]}];
  h.app.selectStudio('a');h.state.caption={text:'old caption'};h.node('caption-text').value='old caption';h.state.approvalKey='old';
  h.app.selectStudio(null);
  assert.equal(h.state.asset,null);assert.equal(h.state.job,null);assert.equal(h.state.caption,null);assert.equal(h.state.approvalKey,null);
  assert.equal(h.node('caption-text').value,'');assert.equal(h.node('export-package').disabled,true);
  assert.equal(JSON.parse(h.stored.get('butler-studio-session')).assetId,null);
  assert.equal(h.state.assets.length,1);
});

test('late inspection response cannot resurrect a removed clip',async()=>{
  const h=harness();h.state.assets=[{...a,validation_status:'validating'}];h.app.selectStudio('a');
  let resolve;h.ctx.fetch=()=>new Promise(r=>resolve=r);
  const pending=h.app.pollWork();h.app.selectStudio(null);
  resolve({ok:true,json:async()=>a});await pending;
  assert.equal(h.state.asset,null);assert.equal(h.state.job,null);
});

test('late Creator Pack cannot attach its text to a replacement clip',async()=>{
  const h=harness();h.state.assets=[a,b];h.app.selectStudio('a');
  let resolve;h.ctx.fetch=()=>new Promise(r=>resolve=r);
  const pending=h.app.generateCaption();h.app.selectStudio('b',null,false);
  resolve({ok:true,json:async()=>({id:'old',text:'for a',hashtags:[],title:'old title'})});await pending;
  assert.equal(h.state.asset.id,'b');assert.equal(h.state.caption,null);assert.equal(h.state.edit.title_text,'');assert.equal(h.node('caption-text').value,'');
});

test('changing edit controls discards the selected old render and disables exports',()=>{
  const h=harness();h.state.assets=[a];h.state.plans=[{id:'p',asset_id:'a'}];h.state.jobs=[{id:'j',plan_id:'p',status:'succeeded',output_keys:[]}];h.app.selectStudio('a');
  h.node('trim-start').value='1';h.node('trim-end').value='4';h.app.collectEdit();
  assert.equal(h.state.job,null);assert.equal(h.node('export-package').disabled,true);assert.equal(h.state.jobs.length,1);
});
