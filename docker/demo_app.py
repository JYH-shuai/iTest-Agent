"""
iTest-Agent Demo 被测系统（阶段三交付物）

一个注册/登录/个人信息的演示 Web 应用，供 MCP 真实执行模式作为被测对象。
刻意埋入 2 个缺陷，用于演示"真实执行 -> 失败用例 -> 缺陷聚类"闭环：

- 缺陷 BUG-001：注册页密码校验文案错误（应提示"密码长度 8-20 位"，实际提示"密码长度 6-20 位"）
- 缺陷 BUG-002：登录失败时无错误提示（应显示"手机号或密码错误"，实际无任何反馈）

启动：
    python docker/demo_app.py          # http://127.0.0.1:8090

路由：
    GET  /             注册/登录页面（单页应用，纯 HTML+JS）
    POST /api/register 注册接口（返回 JSON）
    POST /api/login    登录接口（返回 JSON）
"""

import json
import re
import uuid
from typing import Dict

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="iTest-Agent Demo App", version="1.0.0")

# 内存用户存储（进程内，重启即清空；演示用途）
USERS: Dict[str, Dict] = {}

PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>iTest-Agent 演示系统</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 420px; margin: 48px auto; padding: 0 16px; color: #222; }
  h1 { font-size: 22px; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin-bottom: 16px; }
  label { display: block; margin: 10px 0 4px; font-size: 14px; }
  input { width: 100%; padding: 8px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
  button { width: 100%; padding: 10px; margin-top: 14px; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 15px; }
  .msg { margin-top: 10px; font-size: 14px; min-height: 18px; display: none; }
  .msg.ok, .msg.err { display: block; }
  .ok { color: #16a34a; } .err { color: #dc2626; }
  .nav { display: flex; gap: 12px; margin-bottom: 16px; }
  .nav a { color: #2563eb; cursor: pointer; text-decoration: underline; }
</style>
</head>
<body>
<h1>iTest-Agent 演示系统</h1>
<p>注册 / 登录 / 个人信息 — 供 MCP 真实执行测试</p>

<div class="nav">
  <a id="tab-register" onclick="show('register')">注册</a>
  <a id="tab-login" onclick="show('login')">登录</a>
</div>

<div id="register-card" class="card">
  <h3>用户注册</h3>
  <label for="reg-phone">手机号</label>
  <input id="reg-phone" name="phone" placeholder="11 位手机号">
  <label for="reg-password">密码</label>
  <input id="reg-password" name="password" type="password" placeholder="8-20 位，含字母和数字">
  <label for="reg-nickname">昵称</label>
  <input id="reg-nickname" name="nickname" placeholder="2-20 个字符">
  <button id="reg-submit" onclick="register()">注册</button>
  <div id="reg-msg" class="msg"></div>
</div>

<div id="login-card" class="card" style="display:none">
  <h3>用户登录</h3>
  <label for="login-phone">手机号</label>
  <input id="login-phone" name="phone" placeholder="11 位手机号">
  <label for="login-password">密码</label>
  <input id="login-password" name="password" type="password" placeholder="密码">
  <button id="login-submit" onclick="login()">登录</button>
  <div id="login-msg" class="msg"></div>
</div>

<div id="profile-card" class="card" style="display:none">
  <h3>个人信息</h3>
  <div id="profile-phone" class="msg"></div>
  <div id="profile-nickname" class="msg"></div>
</div>

<script>
function show(tab) {
  document.getElementById('register-card').style.display = tab === 'register' ? 'block' : 'none';
  document.getElementById('login-card').style.display = tab === 'login' ? 'block' : 'none';
  document.getElementById('profile-card').style.display = 'none';
}

async function register() {
  const phone = document.getElementById('reg-phone').value;
  const password = document.getElementById('reg-password').value;
  const nickname = document.getElementById('reg-nickname').value;
  const msg = document.getElementById('reg-msg');
  const resp = await fetch('/api/register', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({phone, password, nickname})
  });
  const data = await resp.json();
  msg.className = 'msg ' + (data.ok ? 'ok' : 'err');
  msg.textContent = data.message;
  // 成功提示保持可见（供自动化断言读取），不自动切换卡片
  if (data.ok) { /* 停留注册页，msg 展示注册成功 */ }
}

async function login() {
  const phone = document.getElementById('login-phone').value;
  const password = document.getElementById('login-password').value;
  const msg = document.getElementById('login-msg');
  const resp = await fetch('/api/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({phone, password})
  });
  const data = await resp.json();
  // BUG-002: 登录失败时前端不做任何提示展示（缺陷埋点）
  if (data.ok) {
    msg.className = 'msg ok';
    msg.textContent = '登录成功，欢迎 ' + (data.nickname || '');
    document.getElementById('profile-phone').textContent = '手机号: ' + (data.phone || '');
    document.getElementById('profile-nickname').textContent = '昵称: ' + (data.nickname || '');
    // 成功提示保持可见（供自动化断言读取）
  } else {
    // 缺陷：失败时无提示
    msg.className = 'msg';
    msg.textContent = '';
  }
}
</script>
</body>
</html>
"""


def _validate_phone(phone: str) -> str:
    """校验手机号，返回错误信息；空串表示通过"""
    if not re.fullmatch(r"1[3-9]\d{9}", phone or ""):
        return "手机号格式不正确，应为 11 位数字"
    return ""


def _validate_password(password: str) -> str:
    """校验密码：8-20 位，含字母和数字"""
    if not password or len(password) < 8 or len(password) > 20:
        # BUG-001: 错误提示文案（应为 8-20，实际写成 6-20）
        return "密码长度需为 6-20 位"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "密码需同时包含字母和数字"
    return ""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return PAGE_HTML


@app.post("/api/register")
async def register(req: Request) -> JSONResponse:
    body = await req.json()
    phone = str(body.get("phone", "")).strip()
    password = str(body.get("password", ""))
    nickname = str(body.get("nickname", "")).strip()

    if err := _validate_phone(phone):
        return JSONResponse({"ok": False, "code": "INVALID_PHONE", "message": err})
    if err := _validate_password(password):
        return JSONResponse({"ok": False, "code": "INVALID_PASSWORD", "message": err})
    if not nickname or len(nickname) < 2 or len(nickname) > 20:
        return JSONResponse({"ok": False, "code": "INVALID_NICKNAME", "message": "昵称长度需为 2-20 个字符"})
    if phone in USERS:
        return JSONResponse({"ok": False, "code": "PHONE_EXISTS", "message": "该手机号已注册"})

    USERS[phone] = {"phone": phone, "password": password, "nickname": nickname, "id": uuid.uuid4().hex[:8]}
    return JSONResponse({"ok": True, "code": "OK", "message": "注册成功"})


@app.post("/api/login")
async def login(req: Request) -> JSONResponse:
    body = await req.json()
    phone = str(body.get("phone", "")).strip()
    password = str(body.get("password", ""))

    user = USERS.get(phone)
    if user and user["password"] == password:
        return JSONResponse({"ok": True, "code": "OK", "message": "登录成功", "phone": phone, "nickname": user["nickname"]})
    return JSONResponse({"ok": False, "code": "AUTH_FAILED", "message": "手机号或密码错误"})


if __name__ == "__main__":
    print("iTest-Agent Demo App: http://127.0.0.1:8090")
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")
