# 打包说明

四个产物都在 `dist/`：

| 文件 | 平台 | 形式 | 说明 |
| --- | --- | --- | --- |
| `pcr-team-builder-windows-x64.zip` | Windows x64 | 便携包 + `安装.bat` | 内置 Python 3.12 / numpy / opencv / Pillow，无需装环境 |
| `pcr-team-builder-android.zip` | Android | Termux + PWA 安装包 | 本环境无 JDK/Android SDK，未生成 APK；内含完整本地服务与安装脚本 |
| `pcr-team-builder-web.zip` | 任意 | 网页端 + 本地服务 | `python start.py` 启动，浏览器访问；已内置 PWA |
| `pcr-team-builder-source.zip` | 任意 | 源代码 | 含脚本、测试、文档、研究缓存 |

## Windows

1. 解压后双击 `安装.bat`（安装到 `%LOCALAPPDATA%\PCRTeamBuilder`，创建桌面/开始菜单快捷方式）；
2. 或直接双击 `start.bat` 免安装运行；
3. 若提示缺少 `msvcp140.dll`，运行 `redist\vc_redist.x64.exe`。

包内 Python 为 embeddable 版，site-packages 已解压 numpy 1.26.4 / opencv-python-headless 4.10 / Pillow 10.4。

## 网页端

```bash
pip install -r requirements.txt   # 只配队可不装
python start.py
```

PWA：Chrome/Edge 打开后「安装应用」。Service Worker 仅在 localhost/HTTPS 下生效。

## Android

```bash
# Termux
cd ~/pcr-team-builder
bash install-termux.sh
bash start-termux.sh
```

然后 Chrome 打开 `http://127.0.0.1:8765/` →「安装应用」。

APK 未生成：当前容器为 aarch64 Linux，无 JDK、Android SDK、Gradle；`aapt2/d8` 也无 aarch64 Linux 官方构建。需要 APK 时请在装有 Android Studio 的机器或 GitHub Actions 上构建 WebView 壳。

## 重新打包

```bash
python3 - <<'PY'
# Windows：解压 build/win/python-embed.zip 到 build/win/pkg/python，
# 将 wheels 解压到 python/Lib/site-packages，复制 pcr_team、start.py、安装/卸载脚本后 zip
PY
```

源码包与网页包可用 `zipfile` 直接重打；源码包排除 `build/ dist/ __pycache__/`。
