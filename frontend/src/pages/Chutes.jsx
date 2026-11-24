import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Chutes() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = () => api('/口门').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const toggle = async (id, 状态) => {
    try { await api(`/口门/${id}/开闭?状态=${encodeURIComponent(状态)}`, { method: 'POST' }); load() }
    catch (e) { setErr(e.message) }
  }
  const calibrate = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api('/口门/校准', { method: 'POST' })
      setMsg(`校准完成：修复 ${r.修复数} 笔（缓存列已整笔回滚到事件真源）`)
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>口门</h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <button type="button" className="secondary" onClick={calibrate}>真源校准（全部）</button>
        <span className="muted">在口件数以路由事件重放为准；缓存件数仅对照，分叉必标。</span>
      </div>
      <table>
        <thead><tr><th>口门码</th><th>标题</th><th>状态</th><th>软容量</th><th>在口件数(真源)</th><th>缓存件数</th><th>分叉</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className={c.分叉 ? 'stale' : ''}>
              <td>{c.code}</td><td>{c.title}</td><td>{c.status}</td><td>{c.soft_cap}</td>
              <td>{c.在口件数}</td><td className="muted">{c.缓存件数}</td>
              <td>{c.分叉 ? <span className="badge-warn">分叉</span> : '一致'}</td>
              <td>
                <button type="button" onClick={() => toggle(c.id, '口门开')}>开</button>{' '}
                <button type="button" className="secondary" onClick={() => toggle(c.id, '口门闭')}>闭</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
