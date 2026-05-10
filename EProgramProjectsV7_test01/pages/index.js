import {useState,useEffect} from 'react';
import {useRouter} from 'next/router';
export default function Login(){
 const r=useRouter(); const [u,su]=useState(''); const [p,sp]=useState(''); const [m,sm]=useState('');
 useEffect(()=>{if(sessionStorage.getItem('auth'))r.replace('/admin')},[]);
 const onSubmit=e=>{e.preventDefault(); if(u&&p){sessionStorage.setItem('auth','1');sessionStorage.setItem('pwd',sessionStorage.getItem('pwd')||'admin');r.push('/admin')}else sm('请输入用户名和密码')};
 return <div style={{maxWidth:360,margin:'80px auto',fontFamily:'sans-serif'}}><h2>管理员登录</h2><form onSubmit={onSubmit}><div>用户名</div><input value={u} onChange={e=>su(e.target.value)} style={{width:'100%',margin:'6px 0 12px'}}/><div>密码</div><input type='password' value={p} onChange={e=>sp(e.target.value)} style={{width:'100%',margin:'6px 0 12px'}}/><button>登录</button>{m&&<p style={{color:'red'}}>{m}</p>}</form></div>
}