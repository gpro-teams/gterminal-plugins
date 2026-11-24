# gTerminal 插件目录

gTerminal 的第三方插件从这里装。gTerminal 在你打开「插件目录」、点「检查更新」时拉
`https://github.com/gpro-teams/gterminal-plugins/releases/latest/download/catalog.json`，不在后台轮询。

- 收进目录的插件都**开源**，用 OSI 认可的许可证（名单见 `scripts/osi-licenses.txt`）。
- 包**由这个仓库的 CI 从源码编出来**：按条目里写的 commit 拉源码、编译、打包，sha256 写进目录。你在 GitHub 上看到的那份源码，就是装上的东西。
- 前期不收费。
- 插件进程用你的身份运行，**没有沙盒**：读文件、联网不受权限限制，权限只管它能调 gTerminal 的哪些能力。装之前看一眼源码。

插件怎么写见 gTerminal SDK（API v1）：一份清单 `gterminal-plugin.json`、一个可执行文件，stdin / stdout 一行一条 JSON-RPC。模板就在这个仓库的 `plugins/hello/`。

## 发布一个插件

1. **源码放在公开的 GitHub 仓库里**：
   - 仓库根上（或者一个子目录里）有 `gterminal-plugin.json`，`runtime.kind` 是 `process`，`runtime.exec` 写相对那个目录的路径；
   - 那个目录里有许可证文件（`LICENSE`）；
   - **Swift 包**（有 `Package.swift`）：CI 用 `swift build -c release --arch arm64 --arch x86_64` 编，把可执行目标放到 `runtime.exec` 写的路径。可执行目标的名字要和 `runtime.exec` 的文件名一样（模板是 `.build/release/hello`）；
   - **脚本**（Python、shell、Node……）：`runtime.exec` 指的文件要在仓库里设成可执行（`git update-index --chmod=+x`），第一行写 `#!`。只能用 macOS 自带的解释器（`/usr/bin/python3` 这类），gTerminal 给插件的 `PATH` 只有 `/usr/bin:/bin:/usr/sbin:/sbin`。
2. **提一个 PR**，在 `plugins/` 下加一个条目文件 `<id>.json`：

   ```json
   {
     "id": "acme.notes",
     "name": "便签",
     "summary": "给每个 agent 贴一张便签，换号、重开都还在",
     "version": "0.1.0",
     "license": "MIT",
     "source": { "repo": "https://github.com/acme/gterminal-notes", "commit": "<完整的 40 位 commit>" }
   }
   ```

   - `id`、`version` 要和清单里的一样；发布者 `gterminal`、`gpro`、`official`、`iderouter` 是保留的。
   - 插件在仓库的子目录里时加 `"path": "<子目录>"`（比如 `"path": "plugins/hello"`）。
   - `commit` 写完整的 40 位，不写分支名：目录收的是那一个版本的源码。
3. **我们看源码**。PR 上的 CI 会按这个 commit 编一遍，编得过、看过没问题再合并。
4. 合并以后 CI 自动发一个新的 Release，gTerminal 下次检查更新就看到了。

**升级**：改条目里的 `version` 和 `commit`，再提 PR。新版本多要了权限，gTerminal 在升级确认里会列出来。

**撤下**（维护者做）：在条目里加 `"withdrawn": "<原因>"`。装了的 gTerminal 拉到目录时自动关掉这个插件、写明原因，用户自己决定要不要再打开；没装的不能再装。尽量撤下，不要删条目：删了以后装了的人看不到原因。

## 包是什么样子

`<id>-<version>.tar.gz`，根上是源码仓库在那个 commit 的文件（`git archive`，只有仓库里的文件；给了 `path` 就是那个子目录），编出来的可执行文件放在 `runtime.exec` 写的路径。时间戳、属主固定：脚本插件的包同一个 commit 每次一样；Swift 编出来的二进制不保证逐字节一样（里面带着路径、UUID），sha256 以 CI 那一次编出来的为准。

gTerminal 装的时候：下载 → 核 sha256（目录里写的）→ 解开 → 包里的清单必须和目录里写的一样 → `runtime.exec` 能执行。哪一步不对都不装。第三方插件没有安装脚本。

## CI 做什么、防什么

`.github/workflows/catalog.yml` 分三段：

| 段 | 权限 | 做什么 |
|---|---|---|
| plan | 只读 | 看哪些条目要重新编：新条目、`version` 或 `commit` 变了的 |
| build | 只读，每个插件单独一个任务 | 拉源码到那个 commit、校验、编译、打包 |
| release | 能写 | 不跑任何插件代码：从包里重算 sha256 和清单，生成 `catalog.json`，发 Release `catalog-<序号>` |

| 做的 | 防什么 |
|---|---|
| 包由 CI 从条目里的 commit 编 | 作者自己上传的二进制和公开的源码不一样（「看过源码」这道关就漏了） |
| sha256 写进目录，gTerminal 装的时候核 | Release 的附件仓库主人随时能换；有了它，装上的就是目录里写的那一个 |
| 编译在只读任务里，发布任务不跑插件代码 | 插件的构建会跑代码（`Package.swift` 解析时就执行）。它拿到写权限就能改整份目录 |
| 一个插件一个构建任务 | 一个插件的构建碰不到别的插件的包 |

不做的：目录签名和单个包签名。它们只比上面多防「这个仓库的 GitHub 账号被盗」，而签名的私钥一放进 CI，就和账号是同一道门，防不住。也不做吊销列表加后台轮询，撤下就是条目里的 `withdrawn`。

## 本机试

```sh
python3 scripts/build.py plan --previous prev.json            # 要编哪些
python3 scripts/build.py package plugins/<id>.json --out dist  # 编一个
python3 scripts/build.py catalog --previous prev.json --dist dist --base-url http://127.0.0.1:8000 --out dist/catalog.json
```

gTerminal 的调试运行可以 `--plugin-catalog http://127.0.0.1:8000/catalog.json` 指到本机的目录（包也放在 127.0.0.1）。

## 第一次发布（维护者）

Hello 的源码和它的条目在同一个仓库里，条目要写源码那个 commit。所以分两步推：

1. 推除了 `plugins/example.hello.json` 以外的全部文件，提交信息里带 `[skip ci]`（这一步不发 Release，免得先发一份空目录）；
2. `git rev-parse HEAD` 取刚才那个 commit，填进 `plugins/example.hello.json` 的 `source.commit`，再推。CI 编 Hello、发第一个 Release `catalog-<序号>`。

以后改 Hello 的源码也一样：先推源码，再改条目里的 `version` 和 `commit`。
