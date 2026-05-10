<template>
  <div class="page">
    <aside><h3>后台导航</h3><button @click="tab='articles'">文章管理</button><button @click="tab='meta'">分类/标签</button></aside>
    <main>
      <h2>{{ tab==='articles'?'文章管理':'分类与标签管理' }}</h2>
      <section v-if="tab==='articles'">
        <div class="bar"><input v-model="kw" placeholder="搜索标题/内容" /><input type="file" @change="up" /><span>{{ fileName||'未上传图片' }}</span></div>
        <div class="grid"><input v-model="form.title" placeholder="文章标题" /><input v-model="form.category" placeholder="分类" /><input v-model="tagInput" placeholder="标签，逗号分隔" /><textarea v-model="form.md" rows="10" placeholder="请输入 Markdown 内容"></textarea><div class="preview" v-html="html"></div></div>
        <button @click="save">保存文章</button>
        <table><tr><th>标题</th><th>分类</th><th>标签</th><th>操作</th></tr><tr v-for="a in paged" :key="a.id"><td>{{a.title}}</td><td>{{a.category}}</td><td>{{a.tags.join('、')}}</td><td><a href="#" @click.prevent="edit(a)">编辑</a> / <a href="#" @click.prevent="del(a.id)">删除</a></td></tr></table>
        <div class="bar"><button :disabled="page===1" @click="page--">上一页</button><span>第 {{page}} / {{pages}} 页</span><button :disabled="page===pages" @click="page++">下一页</button></div>
      </section>
      <section v-else>
        <div class="grid2"><div><h3>分类管理</h3><input v-model="cat" placeholder="新增分类" /><button @click="cats.push(cat);cat=''">添加</button><p>{{cats.join('、')}}</p></div><div><h3>标签管理</h3><input v-model="tag" placeholder="新增标签" /><button @click="tags.push(tag);tag=''">添加</button><p>{{tags.join('、')}}</p></div></div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
const tab=ref('articles'),kw=ref(''),page=ref(1),size=5,fileName=ref('')
const cats=ref(['前端','后端']),tags=ref(['Vue','Node']),cat=ref(''),tag=ref(''),tagInput=ref('Vue,后台')
const list=ref([{id:1,title:'欢迎使用后台',category:'前端',tags:['Vue'],md:'# 你好\n这是预览内容'}])
const form=ref({id:null,title:'',category:'',md:''})
const html=computed(()=>form.value.md.replace(/^# (.*)$/gm,'<h1>$1</h1>').replace(/\n/g,'<br>'))
const filtered=computed(()=>list.value.filter(x=>(x.title+x.md).includes(kw.value)))
const pages=computed(()=>Math.max(1,Math.ceil(filtered.value.length/size)))
const paged=computed(()=>filtered.value.slice((page.value-1)*size,page.value*size))
const save=()=>{const a={...form.value,tags:tagInput.value.split(',').map(s=>s.trim()).filter(Boolean),id:form.value.id||Date.now()};const i=list.value.findIndex(x=>x.id===a.id);i>-1?list.value.splice(i,1,a):list.value.unshift(a);form.value={id:null,title:'',category:'',md:''}}
const edit=a=>{form.value={id:a.id,title:a.title,category:a.category,md:a.md};tagInput.value=a.tags.join(',')}
const del=id=>list.value=list.value.filter(x=>x.id!==id)
const up=e=>fileName.value=e.target.files?.[0]?.name||''
</script>

<style scoped>
.page{display:flex;font-family:Arial}.page>*{padding:16px}aside{width:160px;background:#f7f7f7;height:100vh;display:flex;flex-direction:column;gap:8px}main{flex:1}.bar{display:flex;gap:8px;align-items:center;margin:8px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.grid textarea,.preview{grid-column:span 2}.preview{border:1px solid #ddd;padding:10px;min-height:120px;background:#fff}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}input,textarea,button,table{width:100%;box-sizing:border-box;padding:8px}table{border-collapse:collapse;margin-top:8px}th,td{border:1px solid #ddd;padding:8px;text-align:left}button{background:#1677ff;color:#fff;border:none;border-radius:4px}a{color:#1677ff}
</style>
