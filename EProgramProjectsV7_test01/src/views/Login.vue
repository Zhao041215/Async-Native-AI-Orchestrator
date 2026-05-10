<template>
  <div class="wrap">
    <form class="card" @submit.prevent="go">
      <h2>博客后台登录</h2>
      <input v-model="form.username" placeholder="请输入用户名" />
      <input v-model="form.password" type="password" placeholder="请输入密码" />
      <button>登录</button>
      <p class="tip">演示账号：admin / 123456</p>
    </form>
  </div>
</template>

<script setup>
import { reactive } from 'vue'
import { useRouter } from 'vue-router'
const router = useRouter()
const form = reactive({ username: 'admin', password: '123456' })
const go = () => router.push('/admin')
</script>

<style scoped>
.wrap{height:100vh;display:flex;align-items:center;justify-content:center;background:#f5f7fa}.card{width:320px;padding:24px;background:#fff;border-radius:10px;box-shadow:0 2px 12px #0001;display:flex;flex-direction:column;gap:12px}input,button{padding:10px;border:1px solid #ddd;border-radius:6px}button{background:#1677ff;color:#fff;border:none}.tip{color:#888;font-size:12px}
</style>
