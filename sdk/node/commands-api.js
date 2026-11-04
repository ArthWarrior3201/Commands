// Node.js client for the Commands API. Copy this one file into your app.
//
//   const { Commands } = require("./commands-api");
//   const cx = new Commands();
//   if (cx.available) {
//     const pick = await cx.choose(["Build", "Test"], { title: "What now?" });
//     await cx.cd("C:/Projects/site");
//     cx.close();
//   }
"use strict";
const net = require("net");

class CommandsError extends Error {
  constructor(code, message) {
    super(`${code}: ${message}`);
    this.code = code;
  }
}

class Commands {
  constructor(address = process.env.COMMANDS_API, token = process.env.COMMANDS_API_TOKEN) {
    this.address = address;
    this.token = token;
    this.available = Boolean(address && token);
    this._id = 0;
    this._waiting = new Map();
    this._buf = "";
    this._sock = null;
  }

  _connect() {
    if (this._sock) return this._ready;
    const i = this.address.lastIndexOf(":");
    this._sock = net.createConnection({ host: this.address.slice(0, i), port: Number(this.address.slice(i + 1)) });
    this._ready = new Promise((resolve, reject) => {
      this._sock.once("connect", resolve);
      this._sock.once("error", reject);
    });
    this._sock.on("data", (chunk) => {
      this._buf += chunk.toString("utf8");
      let n;
      while ((n = this._buf.indexOf("\n")) >= 0) {
        const line = this._buf.slice(0, n);
        this._buf = this._buf.slice(n + 1);
        const msg = JSON.parse(line);
        const w = this._waiting.get(msg.id);
        if (!w) continue;
        this._waiting.delete(msg.id);
        msg.error ? w.reject(new CommandsError(msg.error.code, msg.error.message)) : w.resolve(msg.result);
      }
    });
    return this._ready;
  }

  async call(method, params = {}) {
    if (!this.available) throw new CommandsError("no_session", "not started inside a Commands window");
    await this._connect();
    const id = ++this._id;
    return new Promise((resolve, reject) => {
      this._waiting.set(id, { resolve, reject });
      this._sock.write(JSON.stringify({ token: this.token, id, method, params }) + "\n");
    });
  }

  close() { if (this._sock) this._sock.end(); this._sock = null; }

  info() { return this.call("session.info"); }
  cd(path) { return this.call("session.cd", { path }); }
  setenv(name, value) { return this.call("session.setenv", { name, value }); }
  getenv(name) { return this.call("session.getenv", { name }); }
  alias(name, command) { return this.call("session.alias", { name, command }); }
  runAfter(line) { return this.call("session.run", { line }); }
  print(text, markup = true) { return this.call("ui.print", { text, markup }); }
  confirm(question, def = false) { return this.call("ui.confirm", { question, default: def }); }
  ask(question, { def = "", secret = false } = {}) { return this.call("ui.ask", { question, default: def, secret }); }
  choose(items, { title = "", multi = false } = {}) { return this.call("ui.choose", { items, title, multi }); }
  progress(label, value = 0, total = null, done = false) { return this.call("ui.progress", { label, value, total, done }); }
  notify(title, message = "") { return this.call("ui.notify", { title, message }); }
  pathAdd(folder, scope = "user", front = false) { return this.call("path.add", { folder, scope, front }); }
  envSet(name, value, scope = "user") { return this.call("env.set", { name, value, scope }); }
  odus(...args) { return this.call("odus.run", { args }); }
}

module.exports = { Commands, CommandsError };
