// Generic grouped navigation for family HTML reports. Config: the 'lab-navigation' JSON block
// (lab_commons.html.html_document(navigation=...)). The producer supplies items and one hook set
// through LabNavigation.start; it owns only what an item IS and how its panel is drawn.
const LabNavigation=(()=>{
 const pageCount=(length,size)=>Math.max(1,Math.ceil(length/size));
 function page(items,number,size){
  if(!Number.isSafeInteger(number)||number<0||number>=pageCount(items.length,size))throw Error('Navigation page outside coverage');
  return items.slice(number*size,(number+1)*size);
 }
 function index(items,orders,groupOf){
  const byId=new Map(),groups={},positions={},groupKeys={},groupPositions={};
  for(const order of orders){groups[order]=new Map();positions[order]=new Map();}
  items.forEach(item=>{
   if(typeof item.id!=='string'||byId.has(item.id))throw Error('Duplicate or invalid item identity');
   byId.set(item.id,item);
   for(const order of orders){
    const key=groupOf(item,order);if(!groups[order].has(key))groups[order].set(key,[]);
    const members=groups[order].get(key);positions[order].set(item.id,members.length);members.push(item);
   }
  });
  for(const order of orders){groupKeys[order]=[...groups[order].keys()];groupPositions[order]=new Map(groupKeys[order].map((key,i)=>[key,i]));}
  return {byId,groups,positions,groupKeys,groupPositions};
 }
 function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
 // hooks: {items, group(item,order), groupTitle(key,order,members), itemTitle(item,order), show(item,{isCurrent})}
 function start(hooks){
  const config=JSON.parse(document.getElementById('lab-navigation').textContent),size=config.page_size;
  const orders=config.orders.map(order=>order.id),idx=index(hooks.items,orders,hooks.group);
  const mount=document.getElementById(config.mount),status=document.getElementById(config.status);
  const narrow=()=>config.collapse_below_px!==undefined&&window.matchMedia('(max-width:'+config.collapse_below_px+'px)').matches;
  let order=orders[0],groupPage=0,itemPage=0,currentId=null,epoch=0;
  const panel=element('details',undefined,'lab-nav');panel.open=!narrow();panel.append(element('summary',config.label));
  const orderSelect=element('select',undefined,'lab-nav-order'),first=element('select',undefined,'lab-nav-group'),second=element('select',undefined,'lab-nav-item');
  for(const {id,label}of config.orders){const option=element('option',label);option.value=id;orderSelect.append(option);}
  const label=(text,control)=>{const node=element('label',text+' ');node.append(control);return node;};
  const jump=element('details');jump.append(element('summary','Jump directly'),label('Group',first),label('Item',second));
  const itemPages=element('div',undefined,'lab-nav-pages');jump.append(itemPages);
  const groupsNav=element('nav',undefined,'lab-nav-groups');groupsNav.setAttribute('aria-label','Expandable groups');
  if(orders.length>1)panel.append(label('Browse',orderSelect));
  panel.append(jump,element('p','Keyboard: Tab into groups; ↑/↓ move and select, → expands or enters, ← returns or collapses. Enter/Space activates.'),groupsNav);
  mount.replaceChildren(panel);
  const keyOf=item=>hooks.group(item,order),members=key=>idx.groups[order].get(key),groupPosition=key=>idx.groupPositions[order].get(key);
  function pager(number,total,change,noun){
   const controls=element('div',undefined,'lab-nav-controls');
   for(const [text,delta]of [['Previous '+noun,-1],['Next '+noun,1]]){
    const button=element('button',text);button.type='button';button.disabled=number+delta<0||number+delta>=pageCount(total,size);
    button.onclick=()=>change(number+delta);controls.append(button);
   }
   const line=element('span','Page '+(number+1)+' of '+pageCount(total,size)+' · '+total+' '+noun);line.setAttribute('role','status');
   controls.append(line);return controls;
  }
  function choose(item,event){
   const keyboard=event?.detail===0;first.value=String(groupPosition(keyOf(item)));setItems(item.id);
   if(keyboard)return;
   if(narrow())panel.open=false;
   const target=config.scroll_to&&document.getElementById(config.scroll_to);
   if(target){const top=target.getBoundingClientRect().top;if(top<0||top>window.innerHeight)target.scrollIntoView({block:'start'});}
  }
  function renderGroups(){
   const keys=idx.groupKeys[order];
   groupsNav.replaceChildren(element('h2',config.title),pager(groupPage,keys.length,number=>{groupPage=number;setGroups(false);},'groups'));
   for(const key of page(keys,groupPage,size)){
    const items=members(key),group=element('details'),list=element('div');let number=0;
    group.dataset.group=String(groupPosition(key));group.append(element('summary',hooks.groupTitle(key,order,items)+' ('+items.length+' items)'),list);
    const render=()=>{
     list.replaceChildren(pager(number,items.length,value=>{number=value;render();},'items'));
     const ul=element('ul');
     for(const item of page(items,number,size)){
      const li=element('li'),button=element('button',hooks.itemTitle(item,order));
      button.type='button';button.dataset.itemId=item.id;button.setAttribute('aria-current',String(currentId===item.id));
      button.onclick=event=>choose(item,event);li.append(button);ul.append(li);
     }
     list.append(ul);
    };
    group.ontoggle=()=>{
     if(!group.open){list.replaceChildren();return;}
     for(const other of groupsNav.querySelectorAll(':scope>details'))if(other!==group){other.open=false;other.lastElementChild.replaceChildren();}
     render();
    };
    groupsNav.append(group);
   }
  }
  groupsNav.onkeydown=event=>{
   if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key))return;
   const target=event.target.closest('summary,button');
   const nodes=[...groupsNav.querySelectorAll('summary,button')].filter(node=>node.tagName==='SUMMARY'||(node.dataset.itemId&&node.closest('details')?.open));
   const at=nodes.indexOf(target);if(at<0)return;
   event.preventDefault();const group=target.closest('details');let next=target;
   if(event.key==='ArrowDown')next=nodes[Math.min(at+1,nodes.length-1)];
   if(event.key==='ArrowUp')next=nodes[Math.max(at-1,0)];
   if(event.key==='ArrowRight'&&target.tagName==='SUMMARY'){if(!group.open){group.open=true;group.ontoggle();}else next=nodes[at+1]||target;}
   if(event.key==='ArrowLeft'){if(target.tagName==='SUMMARY')group.open=false;else next=group.querySelector('summary');}
   next.focus({preventScroll:true});next.scrollIntoView({block:'nearest',inline:'nearest'});
   if(next.tagName==='BUTTON'&&next.dataset.itemId&&next!==target)next.click();
  };
  function setGroups(reset=true,selectedId){
   if(reset)groupPage=0;
   first.replaceChildren();
   for(const key of page(idx.groupKeys[order],groupPage,size)){
    const option=element('option',hooks.groupTitle(key,order,members(key)));option.value=String(groupPosition(key));first.append(option);
   }
   renderGroups();itemPage=0;
   if(selectedId!==undefined)first.value=String(groupPosition(keyOf(idx.byId.get(selectedId))));
   return setItems(selectedId);
  }
  function setItems(selectedId){
   const items=members(idx.groupKeys[order][Number(first.value)]);
   if(selectedId!==undefined)itemPage=Math.floor(idx.positions[order].get(selectedId)/size);
   second.replaceChildren();
   for(const item of page(items,itemPage,size)){const option=element('option',hooks.itemTitle(item,order));option.value=item.id;second.append(option);}
   itemPages.replaceChildren(pager(itemPage,items.length,number=>{itemPage=number;setItems();},'items'));
   if(selectedId!==undefined)second.value=selectedId;
   return show();
  }
  async function show(){
   const item=idx.byId.get(second.value);if(!item)return;
   const mine=++epoch,home=orders[0],key=hooks.group(item,home);currentId=item.id;
   groupsNav.querySelectorAll('button[data-item-id]').forEach(button=>button.setAttribute('aria-current',String(button.dataset.itemId===item.id)));
   status.removeAttribute('role');
   status.textContent=hooks.groupTitle(key,home,idx.groups[home].get(key))+' / '+hooks.itemTitle(item,home);
   try{await hooks.show(item,{isCurrent:()=>mine===epoch});if(mine===epoch)document.documentElement.dataset.navigationReady='true';}
   catch(error){status.textContent='Error: '+error.message;status.setAttribute('role','alert');console.error(error);}
  }
  orderSelect.onchange=()=>{order=orderSelect.value;setGroups();};
  first.onchange=()=>{itemPage=0;setItems();};second.onchange=show;
  const ready=hooks.items.length===0?(document.documentElement.dataset.navigationReady='true',Promise.resolve()):setGroups();
  return {
   ready,
   current:()=>currentId,
   select(id){
    const item=idx.byId.get(id);if(!item)throw Error('Unknown navigation item');
    groupPage=Math.floor(groupPosition(keyOf(item))/size);return setGroups(false,id);
   },
  };
 }
 return {start,index,page};
})();
