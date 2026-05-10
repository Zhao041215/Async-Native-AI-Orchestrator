import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Admin from '../views/AdminDashboard.vue'

const routes = [
  { path: '/', redirect: '/login' },
  { path: '/login', component: Login },
  { path: '/admin', component: Admin }
]

export default createRouter({
  history: createWebHistory(),
  routes
})
