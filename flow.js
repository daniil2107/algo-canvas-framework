'use strict';

// Legacy canvases have one ordered stage list. Explicit edges and version snapshots are optional.
const copyValue = value => JSON.parse(JSON.stringify(value));
const versionById = id => id ? byId(S.versions, id) : null;
const activeFlowVersion = () => versionById(ui.flowVersionId);

function stagesFor(version) {
    const shared = S.stages.filter(s => !s.catalogOnly);
    if (!version) return shared;
    const ordered = Array.isArray(version.stageOrder)
        ? version.stageOrder.map(id => byId(S.stages, id)).filter(Boolean) : shared;
    return ordered.map(s => version.stageOverrides?.[s.id] || s);
}

function effectiveBlock(version, stage) {
    if (!version || !stage) return null;
    const id = version.pick[stage.id], own = version.blockOverrides?.[stage.id];
    const block = own && own.id === id ? own : byId(S.blocks, id);
    return block?.stage === stage.id ? block : null;
}

function linearEdges(stages) {
    return stages.slice(1).map((s, i) => ({
        id: 'next:' + JSON.stringify([stages[i].id, s.id]), from: stages[i].id, to: s.id, condition: ''
    }));
}

function flowFor(version) {
    const stages = stagesFor(version), ids = new Set(stages.map(s => s.id));
    const edges = version?.flow ?? S.flow ?? linearEdges(stages);
    return edges.filter(e => ids.has(e.from) && ids.has(e.to));
}

function sameFlow(a, b) {
    const signatures = list => list.map(e => JSON.stringify([e.from, e.to, e.condition || ''])).sort();
    return JSON.stringify(signatures(a)) === JSON.stringify(signatures(b));
}

function stageChanged(v, s, base) {
    const left = stagesFor(base), right = stagesFor(v);
    const i = left.findIndex(st => st.id === s.id), j = right.findIndex(st => st.id === s.id);
    if (i !== j || i < 0) return true;
    const fields = st => [st.name, st.job, st.because];
    return JSON.stringify(fields(left[i])) !== JSON.stringify(fields(s))
        || JSON.stringify(effectiveBlock(base, s)) !== JSON.stringify(effectiveBlock(v, s));
}

function stageSnapshot(stage) {
    const out = copyValue(stage);
    delete out.x; delete out.y;
    return out;
}

function snapshotVersion(source) {
    const stages = stagesFor(source);
    const v = {id: uid('v'), name: (source.name || 'Version') + ' fork', parent: source.id,
        pick: {...source.pick}, notes: source.notes || '', stageOrder: stages.map(s => s.id),
        stageOverrides: {}, blockOverrides: {}, flow: copyValue(flowFor(source))};
    for (const s of stages) {
        v.stageOverrides[s.id] = stageSnapshot(s);
        const b = effectiveBlock(source, s);
        if (b) v.blockOverrides[s.id] = copyValue(b);
    }
    return v;
}

let inspectorDraft = null;
function beginInspectorEdit(item, kind, version) {
    const draft = copyValue(item);
    inspectorDraft = {draft, kind, id: item.id, version, model: S, initial: JSON.stringify(draft)};
    return draft;
}
function commitInspectorEdit() {
    const entry = inspectorDraft;
    if (!entry || entry.model !== S) return;
    const next = JSON.stringify(entry.draft);
    if (entry.initial === next) return;
    const {kind, draft, version} = entry;
    const key = kind === 'block' ? 'blockOverrides' : 'stageOverrides';
    const sid = kind === 'block' ? draft.stage : draft.id;
    if (version) {
        if (versionById(version.id) !== version) return;
        (version[key] ??= {})[sid] = draft;
    } else {
        const original = byId(kind === 'block' ? S.blocks : S.stages, draft.id);
        if (!original) return;
        for (const v of S.versions) {
            if (v[key]?.[sid]) continue;
            const assigned = kind === 'block' ? v.pick[sid] === original.id : stagesFor(v).some(s => s.id === sid);
            if (assigned) (v[key] ??= {})[sid] = kind === 'stage' ? stageSnapshot(original) : copyValue(original);
        }
        Object.assign(original, draft);
    }
    entry.initial = next;
}

function setVersionPick(v, block) {
    if (block.status === 'suggested') { toast('Accept this suggestion before using it in a version.'); return false; }
    if (!stagesFor(v).some(s => s.id === block.stage)) { toast('Include this stage in the version first.'); return false; }
    if (!block.why?.trim()) { toast('Write why this block fits the problem before using it.'); return false; }
    v.pick[block.stage] = block.id;
    (v.blockOverrides ??= {})[block.stage] = copyValue(block);
    return true;
}
function removePick(v, sid) {
    const before = JSON.stringify(S);
    delete v.pick[sid];
    if (v.blockOverrides) delete v.blockOverrides[sid];
    ui.sel = {type: 'version', id: v.id}; touch(true); offerUndo('Block removed from ' + v.name + '.', before);
}

function versionPickMenu(v, stage) {
    const current = effectiveBlock(v, stage);
    const selectBox = h('select', {'aria-label': 'Choose a block for ' + stage.name, onpointerdown: stop},
        h('option', {value: ''}, 'Choose a block…'),
        S.blocks.filter(b => b.stage === stage.id && b.status !== 'suggested').map(b =>
            h('option', {value: b.id}, b.name || 'Unnamed block')));
    selectBox.addEventListener('change', () => {
        const b = byId(S.blocks, selectBox.value);
        if (b && setVersionPick(v, b)) touch(true);
    });
    return h('div', {class:'flow-pick'}, selectBox, current ? flowButton('Edit chosen block', () =>
        select('block', current.id, v.id)) : null);
}

function chooseFlow(versionId) {
    ui.flowVersionId = versionId || null;
    select(versionId ? 'version' : null, versionId);
}
function renderFlowPicker() {
    const picker = $('flow-scope');
    if (!versionById(ui.flowVersionId)) ui.flowVersionId = null;
    picker.replaceChildren(h('option', {value:''}, 'Shared stages'),
        ...S.versions.map(v => h('option', {value:v.id}, v.name || 'Unnamed version')));
    picker.value = ui.flowVersionId || '';
}

function changeVersionStages(v, next) {
    const previous = stagesFor(v);
    const oldFlow = flowFor(v), linear = sameFlow(oldFlow, linearEdges(previous));
    const ids = new Set(next);
    v.stageOrder = next;
    v.flow = linear ? linearEdges(stagesFor(v)) : copyValue(oldFlow.filter(e => ids.has(e.from) && ids.has(e.to)));
}
function removeVersionStage(v, id) {
    const before = JSON.stringify(S);
    changeVersionStages(v, stagesFor(v).map(s => s.id).filter(sid => sid !== id));
    delete v.pick[id];
    if (v.blockOverrides) delete v.blockOverrides[id];
    if (v.stageOverrides) delete v.stageOverrides[id];
    ui.sel = {type:'version',id:v.id}; touch(true); offerUndo('Stage removed from ' + v.name + '.', before);
}
function moveVersionStage(v, id, direction) {
    const order = stagesFor(v).map(s => s.id), i = order.indexOf(id), j = i + direction;
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    changeVersionStages(v, order); touch(true);
}
function addStageForVersion(v) {
    // The catalog supplies the stage's stable ID to the theory files. Only this version includes it.
    const order = stagesFor(v).map(s => s.id);
    const s = {id:uid('s'), name:'New stage', job:'', because:[], catalogOnly:true};
    S.stages.push(s);
    changeVersionStages(v, [...order, s.id]);
    ui.sel = {type:'stage', id:s.id, versionId:v.id}; touch(true);
}
function flowButton(label, action, attrs) {
    return h('button', {class:'btn mini',type:'button',onpointerdown:stop,onclick:action,...attrs}, label);
}
function versionStageMenu(v) {
    const ids = stagesFor(v).map(s => s.id);
    const missing = S.stages.filter(s => !ids.includes(s.id));
    const pick = h('select', {'aria-label':'Stage to include'}, missing.map(s=>h('option',{value:s.id},s.name)));
    return h('div',{class:'flow-tools'}, missing.length ? pick : null,
        missing.length ? flowButton('Include stage', () => {changeVersionStages(v,[...ids,pick.value]);touch(true);}) : null,
        flowButton('New stage for this version', () => addStageForVersion(v)));
}

function flowEditor(v, sid) {
    const stages = stagesFor(v), edges = flowFor(v), owner = v || S;
    const box = h('div',{class:'flow-editor'});
    const name = id => byId(stages,id)?.name || id;
    function options() { return stages.map(s=>h('option',{value:s.id}, s.name || s.id)); }
    for (const [i, edge] of edges.entries()) {
        if (sid && edge.from !== sid) continue;
        const label = name(edge.from) + ' → ' + name(edge.to);
        const row = h('div',{class:'flow-edge-row'},
            h('span',{},label),
            h('input',{type:'text',value:edge.condition || '',placeholder:'Always', 'aria-label':'Condition for ' + label,
                onchange:e=>{
                    const next = copyValue(flowFor(v)); next[i].condition=e.target.value.trim();owner.flow=next;touch(true);
                }}),
            flowButton('×',()=>{const before=JSON.stringify(S);owner.flow=flowFor(v).filter((_,j)=>j!==i);touch(true);offerUndo('Arrow removed.',before);},
                {'aria-label':'Remove arrow '+label}));
        box.append(row);
    }
    if (!stages.length) return box;
    const from = h('select',{'aria-label':'Arrow source'}, options());
    const to = h('select',{'aria-label':'Arrow destination'}, options());
    if (sid) {from.value=sid;to.value=sid;}
    const condition = h('input',{type:'text',placeholder:'Always', 'aria-label':'New arrow condition'});
    const add = () => {
        const a = from.value, b = to.value, when=condition.value.trim();
        if (flowFor(v).some(e=>e.from===a && e.to===b && (e.condition || '')===when)) {
            toast('This arrow already exists.');return;
        }
        owner.flow=[...copyValue(flowFor(v)),{id:uid('e'),from:a,to:b,condition:when}];touch(true);
    };
    box.append(h('div',{class:'flow-edge-add'}, sid ? null : h('label',{},'From',from),
        h('label',{},'To',to), h('label',{},'Condition',condition), flowButton('Add arrow',add)));
    return box;
}

function drawFlowLinks() {
    const svg=$('links');
    svg.replaceChildren();
    const make=(tag,attrs)=>{
        const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
        for (const [k,v] of Object.entries(attrs)) el.setAttribute(k,v);
        return el;
    };
    const marker=make('marker',{id:'arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto'});
    marker.append(make('path',{d:'M0 0L10 5L0 10z',fill:'currentColor'}));
    const defs=make('defs',{});defs.append(marker);svg.append(defs);
    const shelves=new Map([...$('frames').querySelectorAll('.shelf')].map(el=>[el.dataset.id,el]));
    const edges=flowFor(activeFlowVersion());
    for (const [i,e] of edges.entries()) {
        const a=shelves.get(e.from), b=shelves.get(e.to);
        if (!a || !b) continue;
        const A=pos.get(frameKey(a)), B=pos.get(frameKey(b));
        if (!A || !B) continue;
        let path, labelX, labelY;
        const forward=B.y > A.y + a.offsetHeight && Math.abs(B.x-A.x)<a.offsetWidth/2;
        if (forward && edges.filter(other=>other.from===e.from).length===1 &&
            stagesFor(activeFlowVersion()).findIndex(s=>s.id===e.to) === stagesFor(activeFlowVersion()).findIndex(s=>s.id===e.from)+1) {
            const x=A.x+23, y=A.y+a.offsetHeight+2, x2=B.x+23, y2=B.y-3;
            path='M '+x+' '+y+' L '+x2+' '+y2;labelX=(x+x2)/2+6;labelY=(y+y2)/2;
        } else {
            const sx=A.x, sy=A.y+a.offsetHeight*.65, tx=B.x-2, ty=B.y+b.offsetHeight*.35;
            const lane=Math.min(A.x,B.x)-22-12*(i%5);
            path='M '+sx+' '+sy+' C '+lane+' '+sy+', '+lane+' '+sy+', '+lane+' '+sy+' L '+lane+' '+ty+' Q '+lane+' '+ty+', '+tx+' '+ty;
            labelX=lane+4;labelY=(sy+ty)/2-4;
        }
        const line=make('path',{d:path,'marker-end':'url(#arrow)',class:'flow-connector'});
        const title=make('title',{});title.textContent=nameOf(a)+' → '+nameOf(b)+(e.condition?' · '+e.condition:'');
        line.append(title);svg.append(line);
        if (e.condition) {
            const label=make('text',{x:labelX,y:labelY,class:'flow-edge-text'});label.textContent=e.condition;svg.append(label);
        }
    }
    function nameOf(el) {return el.querySelector('.head-text .name')?.textContent || el.dataset.id;}
}

function acceptSuggestion(id) {
    const b=byId(S.blocks,id);
    if (!b || b.status!=='suggested') return;
    if (!b.why?.trim()) {toast('Describe why the block fits the problem before accepting it.');return;}
    if (!b.evidence.some(e=>e.source?.trim() && e.where?.trim() && e.says?.trim())) {
        toast('Add a source, location and finding before accepting this suggestion.');return;
    }
    delete b.status;
    for (const v of S.versions) {
        const own=v.blockOverrides?.[b.stage];
        if (own?.id===id) delete own.status;
    }
    touch(true);
}

function newBlockDialog(sid, version) {
    const dialog=h('dialog',{class:'new-block-dialog'});
    const title=h('input',{required:true,'aria-label':'New block name'});
    const why=h('textarea',{required:true,'aria-label':'New block rationale',rows:3});
    const form=h('form',{method:'dialog'}, h('h2',{},'New block'), h('label',{},'Name',title),
        h('label',{},'Why it fits this problem',why),
        h('div',{class:'flow-tools'}, flowButton('Cancel',()=>dialog.close()), h('button',{class:'btn',type:'submit'},'Create block')));
    form.addEventListener('submit',event=>{
        event.preventDefault();
        if (!title.value.trim() || !why.value.trim()) return;
        const liveVersion = version && versionById(version.id);
        const targetExists = stagesFor(liveVersion).some(s => s.id === sid);
        if (!targetExists || (version && !liveVersion)) {dialog.close();toast('The stage or version has changed.');return;}
        const b={id:uid('b'),stage:sid,name:title.value.trim(),does:'',why:why.value.trim(),uses:[],costs:'',evidence:[]};
        if (liveVersion) setVersionPick(liveVersion,b);
        else S.blocks.push(b);
        ui.sel={type:'block',id:b.id,versionId:version?.id};dialog.close();touch(true);
    });
    dialog.append(form);dialog.addEventListener('close',()=>dialog.remove(),{once:true});
    document.body.append(dialog);dialog.showModal();title.focus();
}

function branchLinks(scope, stage) {
    const stages = stagesFor(scope), edges = flowFor(scope).filter(e=>e.from===stage.id);
    const next = stages[stages.findIndex(s=>s.id===stage.id)+1];
    const interesting = edges.filter(e=>edges.length>1 || e.condition || e.to!==next?.id);
    if (!interesting.length) return null;
    return h('div',{class:'stage-branches'},interesting.map(edge=>{
        const target=byId(stages,edge.to);
        const label='→ '+(target?.name || edge.to)+(edge.condition?' · '+edge.condition:'');
        return flowButton(label,()=>select('stage',edge.to,scope?.id));
    }));
}
