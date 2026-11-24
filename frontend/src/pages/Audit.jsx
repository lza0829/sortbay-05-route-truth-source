import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Audit() {
  const [report, setReport] = useState(null)
  const [logs, setLogs] = useState([])
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    setReport(await api('/审计/已路由真源'))
    setLogs(await api('/审计'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const calibrate = async (扫描编号) => {
    setErr(''); setMsg('')
    try {
      const qs = 扫描编号 ? `?扫描编号=${扫描编号}` : ''
      const r = await api(`/口门/校准${qs}`, { method: 'POST' })
      setMsg(`校准完成：修复 ${r.修复数} 笔`)
      load()
    } catch (e) { setErr(e.message) }
  }
  if (err) return <div className="panel err">{err}</div>
  if (!report) return <div className="panel">加载中…</div>
  const s = report.汇总 || {}
  return (
    <div className="panel">
      <h2>审计 · 已路由真源重放</h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="grid">
        <div className="stat"><div className="n">{s.真源已路由数}</div><div className="l">真源已路由</div></div>
        <div className={`stat ${s.分叉数 ? 'stat-warn' : ''}`}><div className="n">{s.分叉数}</div><div className="l">列/事件分叉</div></div>
        <div className="stat"><div className="n">{s.事件总数}</div><div className="l">路由事件总数</div></div>
      </div>
      <div className="row">
        <button type="button" className="secondary" onClick={() => calibrate()}>真源校准（全部）</button>
        <span className="muted">真源=路由事件流水重放；缓存=扫描口门列。旁路改列在此现形并被整笔回滚。</span>
      </div>
      <h3>逐件对账（装载目的地 · 规则口门 · 缓存列 · 真源）</h3>
      <table>
        <thead><tr><th>扫描</th><th>条码</th><th>目的地</th><th>列状态</th><th>缓存口门</th><th>真源口门</th><th>规则口门</th><th>标记</th><th>操作</th></tr></thead>
        <tbody>
          {(report.对账 || []).map((r) => (
            <tr key={r.扫描编号} className={r.分叉 ? 'stale' : ''}>
              <td>{r.扫描编号}</td><td>{r.条码}</td><td>{r.目的地码}</td><td>{r.列状态}</td>
              <td>{r.缓存口门码 || '-'}</td><td>{r.真源口门码 || '-'}</td>
              <td>{r.规则口门码 || '-'}{r.规则漂移 ? ' (漂移)' : ''}</td>
              <td>{r.分叉 ? <span className="badge-warn">{r.陈旧标记}</span> : '一致'}</td>
              <td>{r.分叉 && <button type="button" onClick={() => calibrate(r.扫描编号)}>校准</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>路由事件流水</h3>
      <table>
        <thead><tr><th>事件</th><th>扫描</th><th>口门</th><th>动作</th><th>时间</th><th>备注</th></tr></thead>
        <tbody>
          {(report.事件流水 || []).map((e) => (
            <tr key={e.id}><td>{e.id}</td><td>{e.scan_id}</td><td>{e.chute_id}</td><td>{e.action}</td><td>{e.created_at}</td><td>{e.note}</td></tr>
          ))}
        </tbody>
      </table>
      <h3>审计日志</h3>
      <table>
        <thead><tr><th>编号</th><th>类别</th><th>对象</th><th>动作</th><th>时间</th><th>备注</th></tr></thead>
        <tbody>
          {logs.map((a) => (
            <tr key={a.id}><td>{a.id}</td><td>{a.kind}</td><td>{a.ref_id ?? '-'}</td><td>{a.action}</td><td>{a.created_at}</td><td>{a.note}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
