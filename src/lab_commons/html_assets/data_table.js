// Sortable, filterable data tables for family HTML reports. Markup: lab_commons.html.data_table.
// Every <table data-lab-table> gets click-to-sort headers and, unless data-lab-filter="off", a filter
// box. Pure core (key, compare, matches) is separate from the DOM wiring so it is testable headless.
const LabDataTable=(()=>{
 // Sort key: [rank, value]. Numbers ("1,234", "-0.5", "1e3") rank 0 by value, text ranks 1
 // case-insensitively, empty and "-" rank 2 so they sit last whichever way the column is sorted.
 function key(text){
  const t=String(text).trim(),n=t.replace(/,/g,'');
  if(t===''||t==='-')return [2,''];
  return (n!==''&&Number.isFinite(Number(n)))?[0,Number(n)]:[1,t.toLowerCase()];
 }
 function compare(a,b,descending){
  if(a[0]===2||b[0]===2)return a[0]===b[0]?0:a[0]===2?1:-1;
  const r=a[0]!==b[0]?a[0]-b[0]:a[1]<b[1]?-1:a[1]>b[1]?1:0;
  return descending?-r:r;
 }
 // Space-separated terms AND together, case-insensitively, over the row's text.
 function matches(rowText,query){
  const s=String(rowText).toLowerCase();
  return String(query).toLowerCase().split(/\s+/).filter(Boolean).every(term=>s.includes(term));
 }
 function wire(table){
  if(table.dataset.labWired)return;table.dataset.labWired='1';
  const head=table.tHead?table.tHead.rows[0]:table.rows[0],body=table.tBodies[0];
  if(!head||!body)return;
  const rows=()=>[...body.rows];
  if(table.dataset.labFilter!=='off'){
   const box=document.createElement('input');
   box.type='search';box.className='lab-table-filter';box.placeholder='filter rows (space = AND)';
   const anchor=table.closest('.lab-table-wrap')||table;anchor.parentNode.insertBefore(box,anchor);
   box.addEventListener('input',()=>{for(const row of rows())row.hidden=!matches(row.innerText,box.value);});
  }
  [...head.cells].forEach((th,column)=>{
   th.tabIndex=0;th.setAttribute('aria-sort','none');
   const sort=()=>{
    const descending=th.getAttribute('aria-sort')==='ascending';
    for(const h of head.cells)h.setAttribute('aria-sort','none');
    th.setAttribute('aria-sort',descending?'descending':'ascending');
    const keyed=rows().map(row=>[key(row.cells[column]?row.cells[column].innerText:''),row]);
    keyed.sort((a,b)=>compare(a[0],b[0],descending));
    for(const [,row] of keyed)body.appendChild(row);
   };
   th.addEventListener('click',sort);
   th.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();sort();}});
  });
 }
 function wireAll(root){(root||document).querySelectorAll('table[data-lab-table]').forEach(wire);}
 if(typeof document!=='undefined'){
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>wireAll());else wireAll();
 }
 return {key,compare,matches,wire,wireAll};
})();
