# Git Bash 命令避坑备忘（Windows 环境）

> 场景：pi/终端跑在 Windows + Git Bash（MSYS2/mingw）上，操作本地服务（uvicorn/python/curl）。以下都是实际踩过的坑，按遇到频率排列。

---

## 1. `taskkill /PID` 参数被路径转义 ✅ 用 PowerShell

**错误写法（Git Bash 下必炸）：**
```bash
taskkill /PID 12764 /F
# 报错：无效参数/选项 - 'C:/Program Files/Git/PID'
# 原因：Git Bash 默认把 /xxx 参数转成 Windows 路径（MSYS 路径转换）
```

**正确写法（推荐）：**
```bash
powershell -Command "Stop-Process -Id 12764 -Force"   # 精确 PID 杀进程，符合铁律（禁按名字批量杀）
```

也可以用 cmd 包装（不推荐，转义更麻烦）：
```bash
cmd //c "taskkill /PID 12764 /F"
```

## 2. `/tmp` 路径对 Windows 原生工具不可见 ✅ 临时文件放项目目录

**错误写法：**
```bash
curl --data-binary @/tmp/req.json     # Windows 原生 curl 找不到 /tmp（Git Bash 的虚拟路径）
curl -s -o /tmp/out.txt ...           # 输出文件也同理
```

**正确写法：** 临时文件放在项目目录下（保证编码 UTF-8 + 路径可被双方读到）：
```bash
cat > data/req.json << 'EOF'
{"query":"测试","subject":"math","mode":"A"}
EOF
curl --data-binary @data/req.json http://127.0.0.1:8000/api/chat/send
```
> 注意 heredoc 定界符必须加引号 `'EOF'`（禁变量展开），文件里中文才能完整保留。

## 3. 命令行直接传中文 JSON 必坏 ✅ 先写文件再 --data-binary

**错误写法：** `curl -d '{"query":"你好"}' ...` → 报 "error parsing the body"
**原因：** Git Bash 在 Windows 下命令行参数编码/转义在管道里被破坏。
**正确写法：** 见第 2 条——永远先 `cat > 文件 << 'EOF'` 再 `--data-binary @文件`。

## 4. 输出中文乱码（GBK vs UTF-8）✅ 转码或输出 ASCII

**现象：** python 打印中文、curl 返回中文响应在终端变乱码。
**原因：** Windows 中文系统默认 GBK，Git Bash 终端编码不一致。
**正确做法：**
- 管道转码：`2>&1 | iconv -f GBK -t UTF-8`（注意某些情形 iconv 也救不回，直接读文件为准）
- 脚本里输出英文/数字/ASCII（可读优先）
- 以写入文件的完整内容为准，终端预览只是参考

## 5. 后台启动服务（uvicorn 等）✅ Start-Process 拿新 PID

```powershell
powershell -Command "Start-Process -FilePath 'C:\...\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000' -WorkingDirectory 'C:\...\backend' -WindowStyle Hidden -RedirectStandardOutput 'C:\...\data\uvicorn.log' -RedirectStandardError 'C:\...\data\uvicorn.err.log' -PassThru | Select-Object -ExpandProperty Id"
```
要点：
- `-ArgumentList` 逐项传参，避免引号嵌套
- `-WorkingDirectory` 指定工作目录（os.system/uvicorn 相对路径依赖它）
- `-PassThru | Select-Object -ExpandProperty Id` 直接拿到新进程 PID
- 重定向文件路径避免空格（有空格要处理引号，横生枝节）

## 6. 服务重启的标准流程（示例：后端 on :8000）

```bash
# 1. 查 PID（永远先查再杀，不按名字批量杀）
netstat -ano | findstr ":8000" | findstr LISTENING

# 2. 精确杀掉
powershell -Command "Stop-Process -Id <PID> -Force"
sleep 2
netstat -ano | findstr ":8000" | findstr LISTENING || echo "端口已释放"

# 3. 重新启动（第 5 条写法）并验证
sleep 6
netstat -ano | findstr ":8000" | findstr LISTENING
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/   # 期望 200/404（服务活着即可）

# 4. 接口级验证（第 2 条文件法）
```

## 7. 其它小坑

- `findstr` 过滤中文关键词经常匹配不上（按当前代码页解释），改用 python/powershell 过滤
- `grep -c` / `wc -l` 管道统计中文行数 OK，但内容显示乱码按第 4 条处理
- 嵌套引号灾难：`'{"a":"b'c"}'` 这种必炸，无脑改文件法
- Windows 下 curl 的 `--noproxy "*"` 可以绕过系统代理直连测试外网连通性（代理排查利器）
  ```bash
  curl -s -o /dev/null -w "%{http_code}" --noproxy "*" --max-time 8 https://api.deepseek.com
  # 401 = 网络通（未带 key），000 = 网络不通
  ```

---

## 速查：查代理状态 / 关代理

```powershell
# 查（ProxyEnable=1 则代理开着，ProxyServer 是代理地址）
powershell -Command "Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' | Select-Object ProxyEnable, ProxyServer, ProxyOverride | Format-List"

# 关（把 1 改 0，立即生效，无需重启）
powershell -Command "Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyEnable -Value 0"
```

---

_整理于 2026-08-19，源于 DIFY 考研学习平台排障会话的实际踩坑记录。_