/* Read-only project reference: CAD coordinates never replace saved 3D positions. */
window.Casa3DConduits = (() => {
  const ns = 'http://www.w3.org/2000/svg';
  const state = {floor: null, box: null, piso: true, teto_laje: true, base: '3d'};
  let network, alignment, loading, host;
  const node = (tag, text, attrs = {}) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    return el;
  };
  const svgNode = (tag, attrs) => {
    const el = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    return el;
  };
  async function render(target, floor, panels, openPanel, images) {
    host = target;
    if (state.floor !== floor) { state.floor = floor; state.box = null; }
    if (!network) {
      host.replaceChildren(node('p', 'Carregando eletrodutos…', {role: 'status'}));
      try {
        if (!loading) loading = Promise.all([fetch('assets/conduits.json').then(response => {
          if (!response.ok) throw new Error('Referência indisponível');
          return response.json();
        }), fetch('assets/conduits-alignment.json').then(response => response.ok ? response.json() : {floors:{}}).catch(() => ({floors:{}}))]).catch(error => { loading = null; throw error; });
        [network, alignment] = await loading;
      } catch {
        if (state.floor === floor && !target.hidden) {
          const retry = node('button', 'Tentar novamente', {type: 'button'});
          retry.onclick = () => render(target, floor, panels, openPanel, images);
          target.replaceChildren(node('p', 'Não foi possível carregar a planta de eletrodutos.', {role: 'alert'}), retry);
        }
        return;
      }
      if (state.floor !== floor || target.hidden) return;
    }
    if (!network.floors[floor]) return;
    const info = network.floors[floor];
    const floorRegistration = alignment?.floors[floor];
    const registration = floorRegistration?.image === images?.[floor] ? floorRegistration : floorRegistration?.variants?.[images?.[floor]];
    const canProject = registration && registration.image === images?.[floor];
    const use3d = state.base === '3d' && canProject;
    const projection = use3d ? window.Casa3DProjection.create(registration) : null;
    const selected = info.boxes.find(box => box.id === state.box);
    const section = node('section');
    section.append(node('h2', 'Eletrodutos · ' + (floor === 'terreo' ? 'Térreo' : 'Superior')));
    section.append(node('p', use3d ? 'Projeto sobre a planta 3D · alinhamento visual aproximado · conferir na obra.' : 'Conforme projeto · posição das caixas e execução a conferir na obra.', {class: 'conduit-note'}));
    if (!canProject) section.append(node('p', 'A imagem 3D configurada ainda não tem alinhamento cadastrado. Exibindo a planta técnica.', {class:'conduit-note'}));
    const tools = node('div', null, {class: 'conduit-tools'});
    const base = node('select', null, {'aria-label':'Base dos eletrodutos'});
    for (const [value,label] of [['3d','Planta 3D'],['technical','Planta técnica']]) {
      const option=node('option',label,{value}); option.disabled=value==='3d'&&!canProject; base.append(option);
    }
    base.value=use3d?'3d':'technical';
    base.onchange=()=>{state.base=base.value; render(target,floor,panels,openPanel,images)};
    const baseLabel=node('label','Base');baseLabel.append(base);tools.append(baseLabel);
    for (const [location, label] of [['piso', 'Piso · pontilhado'], ['teto_laje', 'Teto/laje · contínuo']]) {
      const input = node('input', null, {type: 'checkbox'});
      input.checked = state[location];
      input.onchange = () => { state[location] = input.checked; render(target, floor, panels, openPanel, images); };
      const wrapper = node('label'); wrapper.append(input, document.createTextNode(label)); tools.append(wrapper);
    }
    section.append(tools);
    const boxes = node('div', null, {class: 'conduit-tools', 'aria-label': 'Caixas de passagem'});
    for (const box of [{id: null}, ...info.boxes]) {
      const button = node('button', box.id || 'Todos os trechos', {type: 'button', 'aria-pressed': String(state.box === box.id)});
      button.onclick = () => { state.box = box.id; render(target, floor, panels, openPanel, images); };
      boxes.append(button);
    }
    section.append(boxes);
    const detail = selected
      ? `${selected.id} · ${selected.size_mm.join(' × ')} mm · ${selected.connected_routes.length} trechos que chegam à caixa no desenho.`
      : `${info.routes.filter(r => r.location === 'piso').length} trechos de piso · ${info.routes.filter(r => r.location === 'teto_laje').length} de teto/laje · ${info.boxes.length} caixas identificadas.`;
    section.append(node('p', detail, {role: 'status', class: 'conduit-detail'}));
    const boards = node('div', null, {class: 'conduit-tools'});
    boards.append(node('span', 'Quadros reais: depósito · ligações ao projeto a conferir.'));
    for (const [id, panel] of Object.entries(panels || {})) {
      const button = node('button', panel.title, {type: 'button'});
      button.onclick = () => openPanel(id); boards.append(button);
    }
    section.append(boards);
    const [xmin, ymin, xmax, ymax] = info.bounds;
    const width = (xmax - xmin) * 10, height = (ymax - ymin) * 10;
    // Keep the full CAD frame for geometry, but frame the relevant electrical
    // area rather than empty yard/street, especially on the upper floor.
    const points = info.routes.flatMap(route => route.points);
    const left = Math.max(xmin, Math.min(...points.map(p => p[0])) - 2);
    const right = Math.min(xmax, Math.max(...points.map(p => p[0])) + 2);
    const bottom = Math.max(ymin, Math.min(...points.map(p => p[1])) - 2);
    const top = Math.min(ymax, Math.max(...points.map(p => p[1])) + 2);
    const viewWidth = use3d ? registration.width : (right-left)*10;
    const viewBox = use3d ? `0 0 ${registration.width} ${registration.height}` : `${(left-xmin)*10} ${(ymax-top)*10} ${viewWidth} ${(top-bottom)*10}`;
    const svg = svgNode('svg', {viewBox, role: 'img', 'aria-label': `Planta de eletrodutos do ${floor}; piso pontilhado e teto/laje contínuo; conforme projeto.`, class: 'conduit-plan'});
    svg.dataset.base=use3d?'3d':'technical';
    svg.append(svgNode('image', {href: 'assets/' + (use3d ? images[floor] : `conduits-${floor}.svg`), width:use3d?registration.width:width, height:use3d?registration.height:height}));
    for (const route of info.routes) {
      if (!state[route.location]) continue;
      const linked = selected?.connected_routes.includes(route.id);
      const routePoints=use3d?projection.polyline(route.points):route.points.map(([x,y])=>[(x-xmin)*10,(ymax-y)*10]);
      if (!routePoints) continue;
      const d = routePoints.map(([x,y],i)=>`${i?'L':'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
      const line = svgNode('path', {d, fill: 'none', stroke: route.location === 'piso' ? (use3d?'#ffb52e':'#b06010') : (use3d?'#00ddff':'#087c62'), 'stroke-width': use3d?(linked?4:2.4):(linked?2.2:1), opacity: selected && !linked ? .12 : 1, 'data-route': route.id, 'data-location': route.location});
      if (route.location === 'piso') line.setAttribute('stroke-dasharray', use3d?'2 5':'1 2');
      if(use3d){const halo=line.cloneNode();halo.removeAttribute('data-route');halo.removeAttribute('data-location');halo.setAttribute('stroke','#152b36');halo.setAttribute('stroke-width',linked?'6':'4.4');svg.append(halo)}
      const title = svgNode('title', {}); title.textContent = `${route.id} · ${route.location === 'piso' ? 'Piso' : 'Teto/laje'} · Conforme projeto`; line.append(title); svg.append(line);
    }
    for (const box of info.boxes) {
      const position=use3d?projection.point([box.x,box.y]):[(box.x-xmin)*10,(ymax-box.y)*10];
      if(!position)continue;
      const [x,y]=position;
      const boxSize=use3d?10:5.3;
      svg.append(svgNode('rect', {x:x-boxSize/2, y:y-boxSize/2, width:boxSize, height:boxSize, fill:'#fff', stroke:'#252c30', 'stroke-width':1}));
      const label = svgNode('text', {x:x-5, y:y-4, 'text-anchor':'end', fill:'#252c30', stroke:use3d?'#fff':'none', 'stroke-width':use3d?'3':'0', 'paint-order':'stroke', 'data-box-label':''}); label.textContent=box.id; svg.append(label);
    }
    const canvas=node('div',null,{class:'conduit-canvas'});canvas.append(svg);
    if(use3d)for(const [id,panel] of Object.entries(panels||{})){
      if(panel.floor!==floor)continue;
      const button=node('button',null,{type:'button',class:'conduit-panel','aria-label':panel.title+' · Depósito',title:panel.title+' · Depósito'});
      button.style.left=panel.left;button.style.top=panel.top;
      button.append(node('img',null,{src:'assets/'+panel.image,alt:''}));button.onclick=()=>openPanel(id);canvas.append(button);
    }
    section.append(canvas); target.replaceChildren(section);
    const resize = () => {
      if (!svg.isConnected || !svg.clientWidth) return;
      svg.querySelectorAll('[data-box-label]').forEach(label => label.style.fontSize = `${12*viewWidth/svg.clientWidth}px`);
    };
    resize();
    if (host.conduitObserver) host.conduitObserver.disconnect();
    host.conduitObserver = new ResizeObserver(resize); host.conduitObserver.observe(svg);
  }
  return {render};
})();
