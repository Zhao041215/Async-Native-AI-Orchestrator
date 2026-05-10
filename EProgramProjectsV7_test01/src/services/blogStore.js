const {marked}=require('marked');
const hljs=require('highlight.js');
marked.setOptions({highlight:(c,l)=>hljs.highlightAuto(c,l?[l]:undefined).value});
const db={cats:[],arts:[],cid:1,aid:1,admin:{username:'admin',password:'admin123'}};
const hit=(t,q)=>!q||t.toLowerCase().includes(q.toLowerCase());
const hl=(s,q)=>q?s.replace(new RegExp(q,'ig'),m=>`<mark>${m}</mark>`):s;
module.exports={db,render:s=>marked.parse(s||''),
  addCat:b=>{const x={id:db.cid++,name:b.name,sort:b.sort||0};db.cats.push(x);return x;},
  updCat:(id,b)=>Object.assign(db.cats.find(x=>x.id==id)||{},b),
  delCat:id=>db.cats=db.cats.filter(x=>x.id!=id),
  listCat:()=>db.cats.sort((a,b)=>(a.sort||0)-(b.sort||0)),
  addArt:b=>{const x={id:db.aid++,title:b.title,categoryId:+b.categoryId||null,tags:b.tags||[],content:b.content||'',deleted:false,views:0,updatedAt:Date.now()};db.arts.push(x);return x;},
  updArt:(id,b)=>{const x=db.arts.find(x=>x.id==id);if(!x)return null;Object.assign(x,{...b,updatedAt:Date.now()});return x;},
  setDel:(id,v)=>{const x=db.arts.find(x=>x.id==id);if(x)x.deleted=v;return x;},
  getArt:(id,view)=>{const x=db.arts.find(x=>x.id==id);if(x&&view)x.views++;return x;},
  listArt:({page=1,pageSize=15,q='',categoryId,deleted})=>{let a=db.arts.filter(x=>(deleted===undefined||x.deleted===deleted)&&(!categoryId||x.categoryId==categoryId)&& (hit(x.title,q)||hit(x.content,q))).sort((a,b)=>b.updatedAt-a.updatedAt);const total=a.length; a=a.slice((page-1)*pageSize,page*pageSize).map(x=>({...x,titleHighlight:hl(x.title,q)}));return {total,list:a};}
};
