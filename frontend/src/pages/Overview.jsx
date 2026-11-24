import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中…</div>
  const d = data.分布 || {}
  return (
    <div className="panel">
      <h2>总览</h2>
      <p className="muted">当前班次：{data.班次?.business_day} {data.班次?.name} · {data.班次?.sealed ? '已封班' : '未封班'} · 已路由桶按路由事件重放计</p>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
        <div className={`stat ${data.分叉数 ? 'stat-warn' : ''}`}>
          <div className="n">{data.分叉数}</div><div className="l">列/事件分叉</div>
        </div>
      </div>
      <table>
        <thead><tr><th>口门</th><th>标题</th><th>状态</th><th>在口件数(真源)</th><th>缓存件数</th><th>分叉</th></tr></thead>
        <tbody>
          {(data.口门 || []).map((c) => (
            <tr key={c.id} className={c.分叉 ? 'stale' : ''}>
              <td>{c.code}</td><td>{c.title}</td><td>{c.status}</td>
              <td>{c.在口件数}</td><td className="muted">{c.缓存件数}</td>
              <td>{c.分叉 ? <span className="badge-warn">分叉</span> : '一致'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
