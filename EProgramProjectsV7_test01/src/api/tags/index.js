const r=require('express').Router();
const db=globalThis.__contentdb||(globalThis.__contentdb={articles:[],categories:[],tags:[],ids:{a:1,c:1,t:1}});
r.get('/',(_,res)=>res.json(db.tags));
r.get('/:id',(req,res)=>{const x=db.tags.find(v=>v.id===+req.params.id);if(!x)return res.status(404).json({message:'not found'});res.json(x);});
r.post('/',(req,res)=>{const x={id:db.ids.t++,name:(req.body||{}).name||''};db.tags.push(x);res.status(201).json(x);});
r.put('/:id',(req,res)=>{const x=db.tags.find(v=>v.id===+req.params.id);if(!x)return res.status(404).json({message:'not found'});Object.assign(x,req.body||{});res.json(x);});
r.delete('/:id',(req,res)=>{const id=+req.params.id,i=db.tags.findIndex(v=>v.id===id);if(i<0)return res.status(404).json({message:'not found'});db.articles.forEach(a=>a.tagIds=(a.tagIds||[]).filter(t=>t!==id));res.json(db.tags.splice(i,1)[0]);});
module.exports=r;