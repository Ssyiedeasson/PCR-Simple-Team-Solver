# PCR 配队工坊 · 网页端

这是一个自带本地服务的网页应用（规划/识图都在本机运行，不上传数据）。

## 运行

需要 Python 3.10+。

```bash
pip install -r requirements.txt
python start.py
```

浏览器会自动打开 `http://127.0.0.1:8765/`。

Windows 也可双击 `start.bat`；Linux/macOS 用 `./start.sh`。

## 部署到服务器

```bash
python3 -m pcr_team.server --host 0.0.0.0 --port 8765
```

然后用浏览器访问 `http://服务器IP:8765/`。

> 截图识别依赖 opencv/numpy/Pillow；只做配队时可以不装这三个包，服务仍可启动。

## PWA

已内置 `manifest.json` 与 `sw.js`。在 Chrome/Edge 打开后可通过「安装应用 / 添加到主屏幕」
以独立窗口运行（需要 localhost 或 HTTPS）。
