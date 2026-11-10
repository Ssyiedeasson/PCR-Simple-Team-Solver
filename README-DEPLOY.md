# 托管到 GitHub Pages + Render

架构：

```
GitHub Pages（静态前端）  ──HTTPS──▶  Render（Python 后端 /api/*）
```

- Pages：`pcr_team/web` 静态站点，PWA 可用；
- Render：FastAPI 无关，直接跑 `python -m pcr_team.server`，提供配队、识图、API 分析、反馈。

## 1. 创建 GitHub 仓库并添加部署密钥

1. 在 GitHub 新建空仓库（不要勾选 README）；
2. Settings → Deploy keys → Add deploy key；
3. 粘贴 `docs/deploy_key.pub` 的内容，勾选 **Allow write access**；
4. 把仓库 SSH 地址（`git@github.com:用户名/仓库.git`）交给助手推送。

## 2. 部署 Render 后端

1. 用 GitHub 登录 [Render](https://render.com/)；
2. New → Blueprint → 选择本仓库，Render 会读取 `render.yaml`；
3. 部署完成后记下地址，例如 `https://pcr-team-api.onrender.com`；
4. 验证：打开 `https://...onrender.com/api/meta` 应返回 JSON。

免费实例 15 分钟无访问会休眠，首次唤醒约 30–60 秒；
如果识图库导致内存不足，可在 Render 环境变量加 `PCR_RECOG_LIGHT=1`（只加载国服图标库）。

## 3. 配置 Pages

1. 仓库 Settings → Pages → Build and deployment → Source 选 **GitHub Actions**；
2. 打开页面后，在「API 注入 → 后端地址」填入 Render 地址并保存（也可在仓库 Variables 设置 `PCR_API_BASE` 作为默认值）。
3. 推送到 `main` 后，Actions 会自动构建并发布；
4. 访问 `https://用户名.github.io/仓库名/`。

`config.js` 由工作流生成，不要手动改仓库里的同名文件。

## 4. 可选：服务端固定 API Key

公开部署不建议让访客写入配置。Render 环境变量：

```
PCR_PROVIDER=openai
PCR_BASE_URL=https://api.deepseek.com/v1
PCR_MODEL=deepseek-chat
PCR_LLM_API_KEY=sk-...
PCR_DISABLE_CONFIG=1
```

`PCR_DISABLE_CONFIG=1` 会禁用 `/api/config`，避免访客覆盖 Key。

## 5. 本地验证跨域

```bash
# 后端
python -m pcr_team.server --port 8765
# 前端（另一个端口）
cd pcr_team/web && python -m http.server 8890
# config.js 临时改为 http://127.0.0.1:8765
```

浏览器打开 `http://127.0.0.1:8890/`，能正常配队即 CORS 正常。
