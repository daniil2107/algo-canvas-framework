/* The problem graph shares insights with the algorithm canvas. Only explicit
   connections are persisted; the root's links follow from membership in the graph. */
(() => {
    'use strict';
    const kindName = {problem: 'Problem', insight: 'Insight', formulation: 'Similar formulation', theorem: 'Result'};
    const edgeName = {observes: 'Has insight', similar: 'Similar formulation', relevant: 'Relevant result', combines: 'Combination'};
    const fieldNames = {
        problem: [['objective', 'Objective'], ['setting', 'Setting and constraints']],
        insight: [['text', 'Insight'], ['basis', 'Basis or source']],
        formulation: [['title', 'Formulation'], ['detail', 'How it relates to the problem']],
        theorem: [['title', 'Result name'], ['statement', 'Statement'], ['assumptions', 'Assumptions'], ['source', 'Source'], ['where', 'Theorem, section or page']],
    };
    const baseKey = {formulation: 'formulations', theorem: 'theorems', relation: 'relations'};
    let selected = 'problem', draft = null, watcher, mount;
    const button = (label, action, extra = {}) => h('button', {class: 'btn', type: 'button', onclick: action, ...extra}, label);
    const elSvg = (tag, attrs) => {
        const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
        return el;
    };

    function model() {
        const knowledge = S.knowledge || {};
        const nodes = [{id: 'problem', kind: 'problem', title: S.problem.objective || 'Problem', record: S.problem}];
        for (const row of S.insights) nodes.push({id: row.id, kind: 'insight', title: row.text || row.id, record: row});
        for (const kind of ['formulation', 'theorem']) {
            for (const row of knowledge[baseKey[kind]] || []) nodes.push({id: row.id, kind, title: row.title || kindName[kind], record: row});
        }
        const edges = [];
        for (const node of nodes) {
            const kind = node.kind === 'insight' ? 'observes' : node.kind === 'formulation' ? 'similar' : null;
            if (kind) edges.push({id: '@' + kind + ':' + node.id, kind, from: 'problem', to: node.id, derived: true});
        }
        for (const row of knowledge.relations || []) edges.push({...row, derived: false});
        return {nodes, edges, byId: new Map(nodes.map(n => [n.id, n]))};
    }
    function select(id, reveal = true) {
        selected = id; draft = null;
        render(mount);
        if (reveal && mount.clientWidth < 680) mount.querySelector('.kg-inspector')?.scrollIntoView({block: 'nearest'});
    }
    function persist() {
        touch(true);
    }

    function begin(kind, record, endpoint) {
        const isNew = !record;
        const values = {...(record || {})};
        if (isNew && kind === 'relation') Object.assign(values, {kind: 'relevant', from: '', to: '', claim: '', status: 'proposed', source: '', where: ''});
        if (kind === 'relation' && endpoint) {
            if (endpoint.kind === 'theorem') values.to = endpoint.id;
            if (endpoint.kind === 'insight') values.from = endpoint.id;
        }
        // Keep only user changes when an external edit arrives during a draft.
        draft = {kind, isNew, id: record?.id, initial: {...values}, values};

        render(mount);
        const first = mount.querySelector('.kg-edit input, .kg-edit textarea, .kg-edit select');
        first?.focus();
    }

    function sourceLink(value) {
        if (!value) return null;
        let href;
        if (/^https?:\/\//i.test(value)) {
            try { href = new URL(value).href; } catch { return null; }
        } else if (/^theory\/(cards|sources|imported)\//.test(value)) {
            const u = new URL(value, location.origin + '/');
            if (!u.pathname.startsWith('/theory/')) return null;
            href = u.href;
        }
        return href ? h('a', {href, target: '_blank', rel: 'noopener noreferrer'}, value) : null;
    }

    function inspector(data, node, edge) {
        const box = h('aside', {class: 'kg-inspector', 'aria-label': 'Graph details'});
        if (draft) {
            box.append(editForm(data));
            return box;
        }
        if (!node && !edge) return box;
        const title = node ? kindName[node.kind] : edgeName[edge.kind];
        const header = h('div', {class: 'kg-inspector-head'}, h('h2', {}, title));
        const actions = h('div', {class: 'kg-actions'});
        const addDetail = (label, content) => {
            if (content) box.append(h('div', {class: 'kg-field'}, h('div', {class: 'label'}, label), h('div', {class: 'kg-value'}, content)));
        };
        box.append(header);
        if (node) {
            header.append(button('Edit', () => begin(node.kind, node.record), {class: 'btn mini'}));
            for (const [key, label] of fieldNames[node.kind]) addDetail(label, key === 'source' ? sourceLink(node.record[key]) || node.record[key] : node.record[key]);
            if (node.kind === 'insight' || node.kind === 'theorem') actions.append(button('+ Connection', () => begin('relation', null, node)));
            if (node.kind === 'insight') actions.append(button('Show on algorithm', () => showAlgorithmFor(node.id)));
            const incident = data.edges.filter(e => e.from === node.id || e.to === node.id);
            if (incident.length) {
                const list = h('ul', {class: 'kg-neighbors'});
                for (const rel of incident) {
                    const other = data.byId.get(rel.from === node.id ? rel.to : rel.from);
                    if (!other) continue;
                    const item = button(other.title, () => select(rel.id), {class: 'kg-neighbor', title: other.title});
                    item.prepend(h('span', {class: 'kg-relation-kind ' + rel.kind}, edgeName[rel.kind]));
                    list.append(h('li', {}, item));
                }
                box.append(h('h3', {}, 'Connections'), list);
            }
        } else {
            if (!edge.derived) header.append(button('Edit', () => begin('relation', edge), {class: 'btn mini'}));
            const from = data.byId.get(edge.from), to = data.byId.get(edge.to);
            const ends = h('div', {class: 'kg-ends'});
            for (const [n, symbol] of [[from, null], [to, {observes:'→', similar:'↔', relevant:'→', combines:'—'}[edge.kind]]]) {
                if (symbol) ends.append(h('span', {'aria-hidden': 'true'}, symbol));
                if (n) ends.append(button(n.title, () => select(n.id), {class: 'kg-end', title: n.title}));
            }
            box.append(ends);
            if (!edge.derived) {
                addDetail(edge.kind === 'combines' ? 'What follows from the combination' : 'Why the result is relevant', edge.claim);
                addDetail('Evidence status', edge.status === 'supported' ? 'Supported' : 'Proposed');
                addDetail('Source', sourceLink(edge.source) || edge.source);
                addDetail('Theorem, section or page', edge.where);
            }
        }
        if ((node && node.kind !== 'problem') || (edge && !edge.derived)) {
            actions.append(button('Delete', () => remove(node, edge), {class: 'btn kg-delete'}));
        }
        if (actions.childElementCount) box.append(actions);
        return box;
    }
    function remove(node, edge) {
        if (node?.kind === 'insight') {
            selected = 'problem'; draft = null;
            // Keep graph links, shared algorithm references and version-local references in sync.
            delInsight(node.id);
            return;
        }
        const before = JSON.stringify(S);
        if (edge) S.knowledge.relations = S.knowledge.relations.filter(r => r.id !== edge.id);
        else {
            const key = baseKey[node.kind];
            S.knowledge[key] = S.knowledge[key].filter(r => r.id !== node.id);
            S.knowledge.relations = (S.knowledge.relations || []).filter(r => r.from !== node.id && r.to !== node.id);
        }
        selected = 'problem'; draft = null;
        persist();
        if (typeof offerUndo === 'function') offerUndo((node ? kindName[node.kind] : 'Connection') + ' deleted.', before);
    }

    function editForm(data) {
        const {kind, values} = draft;
        const form = h('form', {class: 'kg-edit'});
        form.append(h('h2', {}, (draft.isNew ? 'Add ' : 'Edit ') + (kind === 'relation' ? 'connection' : kindName[kind].toLowerCase())));
        function field(key, label, opts = {}) {
            const id = 'kg-field-' + key;
            let input;
            if (opts.options) {
                input = h('select', {id, required: opts.required}, opts.options.map(([v, text]) => h('option', {value: v}, text)));
            } else input = h(opts.long ? 'textarea' : 'input', {id, required: opts.required, rows: opts.long ? 3 : null});
            input.value = values[key] || '';
            input.addEventListener(opts.options ? 'change' : 'input', () => { values[key] = input.value; opts.change?.(); });
            if (opts.required) input.addEventListener('input', () => input.setCustomValidity(''));
            form.append(h('div', {class: 'kg-control'}, h('label', {for: id}, label), input));
        }
        if (kind === 'relation') {
            const refresh = () => render(mount);
            const options = list => [['', 'Choose a node'], ...list.map(n => [n.id, n.title])];
            const relevant = values.kind === 'relevant';
            field('kind', 'Connection type', {options: [['relevant', 'Insight → relevant result'], ['combines', 'Combination of two insights or two results']], change: () => {
                values.from = ''; values.to = ''; refresh();
            }});
            const first = data.nodes.filter(n => n.kind === 'insight' || (!relevant && n.kind === 'theorem'));
            field('from', relevant ? 'Insight' : 'First node', {required: true, options: options(first), change: () => {
                const a = data.byId.get(values.from), b = data.byId.get(values.to);
                if (!relevant && a && b && (a.kind !== b.kind || a.id === b.id)) values.to = '';
                refresh();
            }});
            const allowed = relevant ? 'theorem' : data.byId.get(values.from)?.kind;
            field('to', relevant ? 'Result' : 'Second node of the same type', {required: true, options: options(data.nodes.filter(n => n.kind === allowed && n.id !== values.from))});
            field('claim', relevant ? 'Why the result is relevant' : 'What follows from combining them?', {required: true, long: true});
            field('status', 'Evidence status', {options: [['proposed','Proposed'],['supported','Supported']], change: refresh});
            field('source', 'Source', {required: values.status === 'supported'});
            field('where', 'Theorem, section or page', {required: values.status === 'supported'});
        } else {
            for (const [key, label] of fieldNames[kind]) {
                const required = kind !== 'problem' && ['title','text','statement'].includes(key);
                field(key, label, {required, long: !['title','source','where'].includes(key)});
            }
        }
        const error = h('p', {class: 'kg-error', role: 'alert', hidden: true});
        form.append(error, h('div', {class: 'kg-actions'}, h('button', {type:'submit', class:'btn kg-primary'}, 'Save'),
            button('Cancel', () => { draft = null; render(mount); })));
        form.addEventListener('submit', ev => {
            ev.preventDefault();
            for (const input of form.querySelectorAll('[required]')) {
                if (!input.value.trim()) { input.setCustomValidity('Enter a value.'); input.reportValidity(); return; }
            }
            const result = commit();
            if (result) { error.hidden = false; error.textContent = result; }
        });
        return form;
    }
    function commit() {
        const {kind, values, initial, isNew, id} = draft;
        const knowledge = S.knowledge ??= {formulations: [], theorems: [], relations: []};
        const rows = kind === 'problem' ? [] : kind === 'insight' ? S.insights : (knowledge[baseKey[kind]] ??= []);
        let row = kind === 'problem' ? S.problem : rows.find(r => r.id === id);
        if (!isNew && !row) return 'This item was removed while you were editing.';
        const updates = {};
        const keys = kind === 'relation' ? ['kind','from','to','claim','status','source','where'] : fieldNames[kind].map(([key]) => key);
        for (const key of keys) {
            const value = (values[key] || '').trim();
            if (isNew || value !== (initial[key] || '')) {
                if (!isNew && (row[key] || '') !== (initial[key] || '') && (row[key] || '') !== value) return 'This field changed on disk. Cancel and reopen the item to review both versions.';
                updates[key] = value;
            }
        }
        const merged = {...row, ...updates};
        if (kind === 'relation') {
            const data = model(), a = data.byId.get(merged.from), b = data.byId.get(merged.to);
            if (!a || !b || a.id === b.id) return 'Choose two different nodes that still exist.';
            if (merged.kind === 'relevant' && (a.kind !== 'insight' || b.kind !== 'theorem')) return 'Choose an insight followed by a result.';
            if (merged.kind === 'combines' && (a.kind !== b.kind || !['insight','theorem'].includes(a.kind))) return 'Combine two insights or two results.';
        }
        if (isNew) {
            const next = 1 + Math.max(0, ...S.insights.map(n => Number(/^I(\d+)$/.exec(n.id)?.[1]) || 0));
            const used = new Set([...model().byId.keys(), ...(knowledge.relations || []).map(r => r.id)]);
            let newId = kind === 'insight' ? 'I' + next : uid(kind[0]);
            while (used.has(newId)) newId = uid(kind[0]);
            row = {id: newId, ...updates}; rows.push(row);
        } else Object.assign(row, updates);
        selected = kind === 'problem' ? 'problem' : row.id; draft = null;
        persist();
        return '';
    }

    function drawMap(board, data, viewWidth) {
        const insights = data.nodes.filter(n => n.kind === 'insight');
        const formulations = data.nodes.filter(n => n.kind === 'formulation');
        const theorems = data.nodes.filter(n => n.kind === 'theorem');
        const columns = theorems.length ? 3 : insights.length ? 2 : 1;
        const width = 176, height = 74, row = 108, padding = 38;
        const gap = Math.max(48, Math.min(130, (viewWidth - 2 * padding - columns * width) / Math.max(1, columns - 1)));
        const contentW = Math.max(viewWidth, 2 * padding + columns * width + (columns - 1) * gap + 20);
        const contentH = 2 * padding + Math.max(1 + formulations.length, insights.length, theorems.length, 1) * row;
        Object.assign(board.style, {width: contentW + 'px', height: contentH + 'px'});
        const positions = new Map(), root = data.byId.get('problem');
        const firstX = Math.max(padding, (contentW - columns * width - (columns - 1) * gap) / 2);
        const addColumn = (nodes, index) => nodes.forEach((n, i) => positions.set(n.id, {x: firstX + index * (width + gap), y: padding + i * row}));
        addColumn([root, ...formulations], 0); addColumn(insights, 1); addColumn(theorems, 2);
        const lines = elSvg('svg', {width:contentW, height:contentH, class:'kg-lines', 'aria-hidden':'true'});
        const defs = elSvg('defs', {});
        for (const kind of ['observes','similar','relevant']) {
            const marker = elSvg('marker', {id:'kg-arrow-' + kind, viewBox:'0 0 10 10', refX:9, refY:5, markerWidth:6, markerHeight:6, orient:'auto-start-reverse'});
            marker.append(elSvg('path', {d:'M 0 0 L 10 5 L 0 10 z', class:'kg-marker ' + kind}));
            defs.append(marker);
        }
        lines.append(defs);
        const nodeButtons = [];
        for (const [i, edge] of data.edges.entries()) {
            const a = positions.get(edge.from), b = positions.get(edge.to);
            if (!a || !b) continue;
            const sameColumn = a.x === b.x;
            const dir = sameColumn && edge.kind === 'similar' ? -1 : 1;
            const ax = a.x + (dir < 0 ? -2 : width + 2), ay = a.y + height / 2;
            const bx = sameColumn ? b.x + (dir < 0 ? -2 : width + 2) : b.x - 2, by = b.y + height / 2;
            // Separate same-column arcs so their elbows remain individually selectable.
            const elbow = sameColumn ? ax + dir * Math.min(42, 23 + 4 * (i % 5)) : (ax + bx) / 2;
            const path = 'M ' + ax + ' ' + ay + ' C ' + elbow + ' ' + ay + ', ' + elbow + ' ' + by + ', ' + bx + ' ' + by;
            const isSelected = selected === edge.id;
            const group = elSvg('g', {class:'kg-edge ' + edge.kind + (isSelected ? ' is-selected' : '')});
            const line = elSvg('path', {d:path, class:'kg-stroke'});
            if (edge.kind !== 'combines') line.setAttribute('marker-end', 'url(#kg-arrow-' + edge.kind + ')');
            if (edge.kind === 'similar') line.setAttribute('marker-start', 'url(#kg-arrow-similar)');
            const hit = elSvg('path', {d:path, class:'kg-hit'});
            hit.addEventListener('click', () => select(edge.id));
            const tip = elSvg('title', {}); tip.textContent = edgeName[edge.kind] + (edge.claim ? ' · ' + edge.claim : '');
            group.append(tip, line, hit);
            if (isSelected) group.dataset.front = 'true';
            lines.append(group);
        }
        const highlighted = lines.querySelector('[data-front]');
        if (highlighted) lines.append(highlighted);
        for (const node of data.nodes) {
            const {x,y} = positions.get(node.id);
            const name = kindName[node.kind];
            const btn = button('', () => select(node.id), {
                // The kind is prefixed: the page has classes named "problem" and "insight" of its own, and one of its
                // rules hides ".problem" while the graph is open.
                class:'kg-node kg-' + node.kind + (selected === node.id ? ' is-selected' : ''),
                'aria-label': name + ' · ' + node.title, 'aria-pressed': String(selected === node.id),
                'data-node':node.id, title:node.title, style:'left:' + x + 'px;top:' + y + 'px;width:' + width + 'px;height:' + height + 'px',
            });
            btn.append(h('span', {class:'kg-type'}, name), h('span', {class:'kg-node-title'}, node.title));
            nodeButtons.push(btn);
        }
        board.replaceChildren(lines, ...nodeButtons);
        typeset(board);
    }

    // A drag moves the diagram, as it moves the algorithm canvas. A press that does not move stays a click.
    function panMap(e, map) {
        if (e.button !== 0 || e.pointerType === 'touch') return;                // a finger scrolls the diagram already
        const box = map.getBoundingClientRect();
        if (e.clientX - box.left >= map.clientWidth || e.clientY - box.top >= map.clientHeight) return;     // a scroll bar
        const sx = e.clientX, sy = e.clientY, left = map.scrollLeft, top = map.scrollTop;
        let moved = false;
        track(ev => {
            const dx = ev.clientX - sx, dy = ev.clientY - sy;
            if (!moved && Math.hypot(dx, dy) < 4) return;
            if (!moved) { moved = true; busyDrag = true; map.classList.add('panning'); }    // no reload from disk mid-drag
            map.scrollLeft = left - dx; map.scrollTop = top - dy;
        }, () => {
            if (!moved) return;
            busyDrag = false; map.classList.remove('panning');
            // The click that ends a drag must not select the node or the connection under the pointer.
            const swallow = ev => { ev.stopPropagation(); ev.preventDefault(); };
            map.addEventListener('click', swallow, {capture: true, once: true});
            setTimeout(() => map.removeEventListener('click', swallow, {capture: true}));
        });
    }

    function render(host) {
        if (!host) return;
        mount = host;
        const oldScroll = host.querySelector('.kg-map-scroll'), oldPanel = host.querySelector('.kg-inspector');
        const scroll = [oldScroll?.scrollLeft || 0, oldScroll?.scrollTop || 0, oldPanel?.scrollTop || 0];
        // Selecting a node redraws the diagram. The keyboard keeps scrolling it only if the focus stays inside.
        const held = oldScroll?.contains(document.activeElement) ? document.activeElement.dataset.node || '' : null;
        const activeField = document.activeElement?.closest('.kg-edit') ? document.activeElement.id : null;
        const caret = activeField && document.activeElement.tagName === 'TEXTAREA' ? document.activeElement.selectionStart : null;
        watcher?.disconnect();
        const data = model();
        if (selected !== 'problem' && !data.byId.has(selected) && !data.edges.some(e => e.id === selected)) selected = 'problem';
        const toolbar = h('div', {class:'kg-tools', role:'group', 'aria-label':'Add to problem graph'});
        for (const [label, kind] of [['Insight','insight'],['Formulation','formulation'],['Result','theorem'],['Connection','relation']]) {
            toolbar.append(button('+ ' + label, () => begin(kind)));
        }
        const legend = h('div', {class:'kg-legend', 'aria-label':'Connection types'});
        for (const [kind, name] of Object.entries(edgeName)) {
            legend.append(h('span', {}, h('i', {class:'kg-key ' + kind, 'aria-hidden':'true'}), name));
        }
        const board = h('div', {class:'kg-board'});
        const map = h('div', {class:'kg-map-scroll', tabindex:'0', role:'region', 'aria-label':'Graph diagram, scroll or drag to explore'}, board);
        map.addEventListener('pointerdown', e => panMap(e, map));
        const panel = inspector(data, data.byId.get(selected), data.edges.find(e => e.id === selected));
        const shell = h('section', {class:'kg-shell', 'aria-label':'Problem graph'},
            h('div', {class:'kg-main'}, toolbar, map, legend), panel);
        host.replaceChildren(shell);
        let lastWidth = map.clientWidth;
        drawMap(board, data, lastWidth);
        map.scrollLeft = scroll[0]; map.scrollTop = scroll[1]; panel.scrollTop = scroll[2];
        if (held !== null && !draft) {
            const again = held && [...board.querySelectorAll('.kg-node')].find(n => n.dataset.node === held);
            (again || map).focus({preventScroll:true});
        }
        if (activeField && draft) {
            const input = host.querySelector('#' + CSS.escape(activeField));
            if (input) {
                input.focus({preventScroll:true});
                if (caret !== null && input.tagName === 'TEXTAREA') input.setSelectionRange(caret, caret);
            }
        }
        typeset(panel);
        if (typeof ResizeObserver === 'function') {
            watcher = new ResizeObserver(() => {
                if (map.clientWidth === lastWidth || !map.clientWidth) return;
                lastWidth = map.clientWidth;
                drawMap(board, data, lastWidth);
            });
            watcher.observe(map);
        }
    }
    window.renderKnowledgeGraph = render;
    window.selectKnowledgeNode = select;
})();
