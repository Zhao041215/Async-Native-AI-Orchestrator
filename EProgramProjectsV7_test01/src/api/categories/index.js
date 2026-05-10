const r=require('express').Router();
const db=globalThis.__contentdb||(globalThis.__contentdb={articles:[],categories:[],tags:[],ids:{a:1,c:1,t:1}});
r.get('/',(_,res)=>res.json(db.categories));
r.get('/:id',(req,res)=>{const x=db.categories.find(v=>v.id===+req.params.id);if(!x)return res.status(404).json({message:'not found'});res.json(x);});
r.post('/',(req,res)=>{const x={id:db.ids.c++,name:(req.body||{}).name||'',description:(req.body||{}).description||''};db.categories.push(x);res.status(201).json(x);});
r.put('/:id',(req,res)=>{const x=db.categories.find(v=>v.id===+req.params.id);if(!x)return res.status(404).json({message:'not found'});Object.assign(x,req.body||{});res.json(x);});
r.delete('/:id',(req,res)=>{const id=+req.params.id,i=db.categories.findIndex(v=>v.id===id);if(i<0)return res.status(404).json({message:'not found'});db.articles.forEach(a=>{if(a.categoryId===id)a.categoryId=null});res.json(db.categories.splice(i,1)[0]);});
module.exports=r;