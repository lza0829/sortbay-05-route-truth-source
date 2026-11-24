import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Scans() {
  const [rows, setRows] = useState([])
  const [chutes, setChutes] = useState({})
  const [barcode, setBarcode] = useState('')
  const [dest, setDest] = useState('110')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [receipt, setReceipt] = useState(null)
  const load = () => api('/扫描').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => {
    load()
    api('/口门').then((cs) => setChutes(Object.fromEntries(cs.map((c) => [c.id, c.code])))).catch(() => {})
  }, [])
  const create = async () => {
    setErr(''); setMsg('')
    try {
      await api('/扫描', { method: 'POST', body: JSON.stringify({ 条码: barcode, 目的地码: dest }) })
      setMsg('过机装载成功'); setBarcode(''); load()
    } catch (e) { setErr(e.message) }
  }
  const routeOne = async (id) => {
    setErr(''); setMsg(''); setReceipt(null)
    try {
      const r = await api(`/扫描/${id}/路由`, { method: 'POST' })
      setReceipt(r)
      setMsg(`裁决成功：事件#${r.事件.id} 真源口门 ${chutes[r.真源.口门] || r.真源.口门}`)
      load()
    } catch (e) { setErr(e.message) }
  }
  const 口门码 = (id) => (id == null ? '-' : (chutes[id] || `#${id}`))
  return (
    <div className="panel">
      <h2>过机扫描 · 路由裁决</h2>
      <div className="row">
        <label>条码<input value={barcode} onChange={(e) => setBarcode(e.target.value)} /></label>
        <label>目的地码<input value={dest} onChange={(e) => setDest(e.target.value)} /></label>
        <button type="button" onClick={create}>装载扫描</button>
      </div>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      {receipt && (
        <div className="receipt">
          <b>裁决回执</b>（扫描册/口门在口/总览已同刷）：
          事件#{receipt.事件.id} · 真源口门 {口门码(receipt.真源.口门)} ·
          在口 {receipt.口门在口.map((c) => `${c.口门码}:${c.在口件数}`).join(' ') || '-'} ·
          已路由桶 {receipt.总览.分布.已路由} · 分叉 {receipt.总览.分叉数}
        </div>
      )}
      <table>
        <thead><tr><th>编号</th><th>条码</th><th>目的地</th><th>有效状态</th><th>真源口门</th><th>缓存列</th><th>分叉</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.分叉 ? 'stale' : ''}>
              <td>{r.id}</td><td>{r.barcode}</td><td>{r.dest_code}</td><td>{r.有效状态}</td>
              <td>{口门码(r.真源口门)}</td>
              <td className="muted">{r.status}/{口门码(r.chute_id)}</td>
              <td>{r.分叉 ? <span className="badge-warn">{r.陈旧标记}</span> : '一致'}</td>
              <td>{(r.status === '待路由' || r.status === '错分回流') && <button type="button" onClick={() => routeOne(r.id)}>路由</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
