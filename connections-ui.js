'use strict';

async function openStageTheory(stageId, blockId, versionId) {
    const version = versionById(versionId);
    const stage = stagesFor(version).find(s => s.id === stageId);
    if (!stage) return;
    const block = blockId && (effectiveBlock(version, stage)?.id === blockId
        ? effectiveBlock(version, stage) : byId(S.blocks, blockId));
    const params = new URLSearchParams({stage: stageId});
    if (block) params.set('block', block.id);
    if (version) params.set('version', version.id);
    const dismiss = () => modal.close();
    const body = h('div', {class: 'theory-body'}, 'Searching local results…');
    const modal = h('dialog', {class: 'theory-dialog', 'aria-label': 'Find a connection'},
        h('header', {}, h('div', {}, h('small', {}, 'Theory for this stage'), h('h2', {}, stage.name)),
            h('button', {type: 'button', class: 'btn', onclick: dismiss}, 'Close')), body);
    document.body.append(modal);
    modal.addEventListener('close', () => modal.remove(), {once: true});
    modal.showModal();
    try {
        for (let i = 0; i < 50 && (saving || dirty) && online; i++)
            await new Promise(done => setTimeout(done, 100));
        if (!online || saving || dirty) throw Error('Save the canvas before searching its current stage.');
        const response = await fetch('/api/connections?' + params, {cache: 'no-store'});
        const data = await response.json();
        if (!response.ok) throw Error(data.error || 'Could not read the cards.');
        if (!modal.open) return;
        const results = data.results;
        if (!results.length) {
            body.textContent = 'No local result matches this stage yet. Use the paper decomposition skill to import a relevant passage, then search again.';
            return;
        }
        const picker = h('select', {'aria-label': 'Source result'}, results.map(r => h('option', {value: r.id}, r.title)));
        const preview = h('div', {class: 'theory-preview'});
        const draw = () => showTheoryResult(preview, results.find(r => r.id === picker.value), stage, block, version, dismiss);
        picker.addEventListener('change', draw);
        body.replaceChildren(h('label', {}, 'Source result', picker), preview);
        draw();
    } catch (err) { body.textContent = err.message; }
}

function showTheoryResult(host, result, stage, block, version, dismiss) {
    host.replaceChildren();
    const linked = new Set([...stage.because, ...(block?.uses || [])]);
    const ranked = [...S.insights].sort((a,b) => Number(linked.has(b.id)) - Number(linked.has(a.id)));
    const insightPick = h('select', {'aria-label':'Insight to connect'},
        !linked.size && h('option', {value:''}, 'Choose an insight'),
        ranked.map(i => h('option', {value:i.id}, i.id + ' · ' + (i.text || 'Untitled insight'))));
    const source = /^https?:\/\//i.test(result.source)
        ? h('a', {href:result.source,target:'_blank',rel:'noopener noreferrer'}, 'Primary source')
        : h('span', {}, result.source || 'Local record');
    const note = h('p', {class:'theory-notice',role:'status'});
    const btn = (label, fn) => h('button', {type:'button',class:'btn',onclick:fn}, label);
    const attach = btn('Connect to insight', () => {
        const insight = byId(S.insights, insightPick.value);
        if (!insight) { note.textContent = 'Choose an insight to attach this result.'; return; }
        const k = S.knowledge;
        let t = k.theorems.find(t => t.cardId === result.id || (t.source === result.source && t.where === result.where && t.title === result.title));
        if (!t) {
            t = {id:uid('T'),title:result.title,statement:result.passage,assumptions:result.assumptions,
                source:result.source,where:result.where,cardId:result.id};
            k.theorems.push(t);
        }
        if (k.relations.some(e => e.kind === 'relevant' && e.from === insight.id && e.to === t.id)) {
            note.textContent = 'This insight is already connected to that result.'; return;
        }
        k.relations.push({id:uid('r'),kind:'relevant',from:insight.id,to:t.id,
            claim:'Check whether the assumptions of “' + result.title + '” hold for this insight.',
            status:'proposed',source:result.source,where:result.where});
        touch(true);
        note.textContent = 'Proposed connection saved in the problem graph.';
    });
    const input = (label, value, single) => {
        const el = h(single ? 'input' : 'textarea', {'aria-label':label,rows:single ? null : 2});
        el.value = value;
        return {el,label:h('label', {}, label, el)};
    };
    const name = input('Alternative name', result.title, true);
    const does = input('Change to this stage', result.suggestion || '', false);
    const why = input('Why it fits this problem', '', false);
    const costs = input('Tradeoff or test', '', false);
    const versions = S.versions.filter(v => stagesFor(v).some(s => s.id === stage.id));
    const choose = h('select', {'aria-label':'Fork from'}, versions.map(v => h('option', {value:v.id}, v.name)));
    if (versions.some(v => v.id === version?.id)) choose.value = version.id;
    else if (versions.some(v => v.id === baseline()?.id)) choose.value=baseline().id;
    const uses = [...linked];
    const tryInFork = btn('Try in a fork', () => {
        const parent = versionById(choose.value);
        if (!parent) { note.textContent = 'Create a version containing this stage before forking.'; return; }
        if (!name.el.value.trim() || !does.el.value.trim() || !why.el.value.trim()) {
            note.textContent = 'Give the alternative a name, a concrete change, and a reason it fits.'; return;
        }
        const fork = snapshotVersion(parent);
        const candidate = {id:uid('trial'),stage:stage.id,status:'trial',name:name.el.value.trim(),
            does:does.el.value.trim(),why:why.el.value.trim(),costs:costs.el.value.trim(),
            uses:[...new Set([...uses,insightPick.value])].filter(id=>PROBLEM_PARTS.has(id)||byId(S.insights,id)),
            evidence:[{source:result.card,where:result.label+' · '+result.where,says:result.passage+(result.assumptions ? " Conditions: "+result.assumptions : ""),cardId:result.id,origin:result.source}]};
        fork.pick[stage.id] = candidate.id;
        fork.blockOverrides[stage.id] = candidate;
        fork.name = parent.name + ' · ' + candidate.name;
        S.versions.push(fork); ui.flowVersionId = fork.id; ui.sel = {type:'version',id:fork.id};
        dismiss(); touch(true); toast('Alternative saved in its own version.');
    });
    host.append(h('p', {class:'source-link'}, source, ' · ', result.where),
        h('p',{},result.passage), h('p',{},h('b',{},'Assumptions. '),result.assumptions || 'Unspecified in this record.'),
        h('div',{class:'theory-action'},insightPick,attach), h('details',{}, h('summary',{},'Develop an alternative'),
        h('div',{class:'theory-form'}, name.label,does.label,why.label,costs.label,
        h('label',{},'Fork from',choose),tryInFork)), note);
    typeset(host);
}

function openResearchGuide() {
    const panel = h('dialog',{class:'theory-dialog','aria-label':'Research workflows'});
    const close = h('button',{type:'button',class:'btn',onclick:()=>panel.close()},'Close');
    const ctx = currentItem(), stage = ui.sel?.type === 'block' ? ctx?.stage : ui.sel?.type === 'stage' ? ctx?.id : null;
    const versionId = ui.sel?.versionId || (ui.sel?.type === 'version' ? ui.sel.id : ui.flowVersionId);
    const subject = stage ? 'stage ' + stage : 'one stage of this algorithm';
    const node = !$('knowledgeHost').hidden && $('knowledgeHost').querySelector('.kg-node.is-selected')?.dataset.node;
    const target = node && node !== 'problem' ? 'graph node ' + node : 'an insight in this problem';
    const message = h('p',{class:'theory-notice',role:'status'});
    function prompt(title, text) {
        const code = h('textarea',{'aria-label':title+' prompt',rows:3,readonly:true});
        code.value=text;
        const copy = h('button',{class:'btn',type:'button',onclick:async () => {
            try {await navigator.clipboard.writeText(text);message.textContent='Prompt copied.';}
            catch {code.focus();code.select();message.textContent='The prompt is selected for copying.';}
        }},'Copy prompt');
        return h('details',{},h('summary',{},title),h('div',{class:'theory-form'},code,copy));
    }
    panel.append(h('header',{},h('h2',{},'Research workflows'),close),
        h('div',{class:'theory-body'},
        h('p',{},'Use these prompts in a coding agent opened in this project. It reads the same problem graph and cards through the local canvas MCP server.'),
        prompt('Decompose a paper', '$canvas-decompose-paper — Decompose the attached paper into results with assumptions and locators. Connect the relevant insights and stages to the current problem.'),
        prompt('Connect a result', '$canvas-connect-theorem — Follow '+target+' and find a relevant result. Check its assumptions and add the connection as proposed.'),
        prompt('Develop an alternative', '$canvas-explore-alternative — Decompose '+subject+(versionId?' in version '+versionId:'')+' and develop one alternative. Trace its rationale to the graph, identify the tradeoff and propose a test.'),
        message));
    document.body.append(panel);panel.addEventListener('close',()=>panel.remove(),{once:true});panel.showModal();
}
