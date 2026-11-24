#!/usr/bin/env python3
# gTerminal 插件目录的构建脚本（MIT）。只用 Python 标准库，CI（.github/workflows/catalog.yml）分三段调它：
#
#   plan     --previous prev.json                          → 打印要重新编的插件 id（JSON 数组）：新条目、版本或 commit 变了的
#   package  plugins/<id>.json --out dist                  → 拉源码到条目写的 commit、校验、编译（Swift 包）、打成可复现的 tar.gz
#   catalog  --previous prev.json --dist dist --base-url U → 生成 catalog.json：这次编的用新包（sha256 在这里重算），
#                                                            没变的照抄上一份目录里的那条，withdrawn / 名字 / 说明按条目文件
#
# package 跑插件自己的构建（Package.swift 在解析时就会执行代码），所以 CI 里它在只读权限的任务里跑；
# catalog 不跑任何插件代码，只读包和条目文件，在有写权限的发布任务里跑。
#
# 包的样子（gTerminal 的安装器照这个认）：tar.gz，根上是源码仓库在那个 commit 的全部文件（git archive），
# 编出来的可执行文件放在清单 runtime.exec 写的那个路径。时间戳、属主固定：脚本插件的包同一个 commit 每次一样；
# 编译出来的二进制（Swift）不保证逐字节一样（里面带着路径、UUID），所以 sha256 以 CI 这一次编出来的为准。
import argparse, gzip, hashlib, io, json, os, re, shutil, subprocess, sys, tarfile, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "plugins")
ID_RE = re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+$")
RESERVED = {"gterminal", "gpro", "official", "iderouter"}
MAX_PACKAGE = 50 << 20


def die(msg):
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(1)


def osi_licenses():
    with open(os.path.join(ROOT, "scripts", "osi-licenses.txt"), encoding="utf-8") as f:
        return {l.strip() for l in f if l.strip() and not l.startswith("#")}


def load_entry(path):
    with open(path, encoding="utf-8") as f:
        e = json.load(f)
    pid = os.path.basename(path)[: -len(".json")]
    problems = []
    if e.get("id") != pid:
        problems.append(f"文件名要是 <id>.json（文件是 {pid}，id 是 {e.get('id')}）")
    if not ID_RE.match(e.get("id", "")):
        problems.append("id 要写成「发布者.名字」（小写字母、数字、横线）")
    elif e["id"].split(".")[0] in RESERVED:
        problems.append(f"发布者 {e['id'].split('.')[0]} 是保留的")
    for k in ("name", "version", "license"):
        if not isinstance(e.get(k), str) or not e[k]:
            problems.append(f"要有 {k}")
    if e.get("license") and e["license"] not in osi_licenses():
        problems.append(f"许可证 {e['license']} 不在 OSI 认可的名单里（scripts/osi-licenses.txt）")
    src = e.get("source") or {}
    if not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+$", src.get("repo", "")) and not ALLOW_LOCAL:
        problems.append("source.repo 要是 https://github.com/<账号>/<仓库>")
    if not re.match(r"^[0-9a-f]{40}$", src.get("commit", "")):
        problems.append("source.commit 要写完整的 40 位 commit")
    path = src.get("path")
    if path is not None and (not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/")):
        problems.append("source.path 要写仓库里的相对目录（不许 .. 和绝对路径）")
    unknown = set(e) - {"id", "name", "summary", "version", "license", "source", "withdrawn"}
    if unknown:
        problems.append(f"不认识的字段：{', '.join(sorted(unknown))}")
    if problems:
        die(f"{path}：" + "；".join(problems))
    return e


def entries():
    out = []
    for n in sorted(os.listdir(PLUGINS)):
        if n.endswith(".json"):
            out.append(load_entry(os.path.join(PLUGINS, n)))
    return out


def read_previous(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
    return {p["id"]: p for p in c.get("plugins", [])}


def needs_build(e, prev):
    p = prev.get(e["id"])
    return p is None or p.get("version") != e["version"] or (p.get("source") or {}).get("commit") != e["source"]["commit"]


# ---------------- plan ----------------

def cmd_plan(a):
    prev = read_previous(a.previous)
    print(json.dumps([e["id"] for e in entries() if needs_build(e, prev)]))


# ---------------- package ----------------

def run(argv, cwd=None):
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"{' '.join(argv)} 失败：{(r.stderr or r.stdout).strip()[-800:]}")
    return r.stdout


def cmd_package(a):
    e = load_entry(a.entry)
    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="gtp-")
    try:
        src = os.path.join(work, "src")
        commit = e["source"]["commit"]
        run(["git", "clone", "--quiet", "--no-checkout", a.clone_from or e["source"]["repo"], src])
        run(["git", "checkout", "--quiet", "--detach", commit], cwd=src)
        if run(["git", "rev-parse", "HEAD"], cwd=src).strip() != commit:
            die("拉下来的不是条目里写的 commit")
        # 包里的东西：git archive（只有仓库里的文件，没有 .git、没有构建残留）；source.path 给了就只打那个目录（插件在仓库的子目录里）
        sub = e["source"].get("path") or ""
        stage = os.path.join(work, "stage")
        os.makedirs(stage)
        tree = f"{commit}:{sub}" if sub else commit
        r = subprocess.run(["git", "archive", "--format=tar", tree], cwd=src, capture_output=True)
        if r.returncode != 0:
            die(f"仓库在 {commit[:7]} 里没有目录 {sub}")
        safe_extract(r.stdout, stage)
        build_dir = os.path.join(src, sub) if sub else src
        mpath = os.path.join(stage, "gterminal-plugin.json")
        if not os.path.exists(mpath):
            die("仓库根上没有 gterminal-plugin.json")
        with open(mpath, encoding="utf-8") as f:
            m = json.load(f)
        if m.get("id") != e["id"] or m.get("version") != e["version"]:
            die(f"清单是 {m.get('id')} {m.get('version')}，条目写的是 {e['id']} {e['version']}")
        rt = m.get("runtime") or {}
        exe = rt.get("exec") or ""
        if rt.get("kind") != "process" or not exe or exe.startswith("/") or ".." in exe:
            die("清单的 runtime 要是 process，exec 写相对仓库根的路径（不许 .. 和绝对路径）")
        v1 = v1_problems(m)
        if v1:
            die("；".join(v1))
        if not any(os.path.exists(os.path.join(stage, n)) for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")):
            die("仓库根上要有许可证文件（LICENSE）")
        if os.path.exists(os.path.join(stage, "Package.swift")):
            # Swift 包：编 arm64 + x86_64 两个架构，产物放到 runtime.exec 写的路径
            argv = ["swift", "build", "-c", "release", "--arch", "arm64", "--arch", "x86_64"]
            run(argv, cwd=build_dir)
            # 产物放在哪随工具链版本变（.build/apple/…、.build/out/…），问 SwiftPM 自己
            product = os.path.join(run(argv + ["--show-bin-path"], cwd=build_dir).strip().splitlines()[-1], os.path.basename(exe))
            if not os.path.isfile(product):
                die(f"编完没找到 {os.path.basename(exe)}（可执行目标的名字要和 runtime.exec 的文件名一样）")
            dest = os.path.join(stage, exe)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(product, dest)
            os.chmod(dest, 0o755)
        target = os.path.join(stage, exe)
        if not os.path.isfile(target) or not os.access(target, os.X_OK):
            die(f"runtime.exec 不是可执行文件：{exe}（脚本要在仓库里设成可执行，第一行写 #!）")
        name = f"{e['id']}-{e['version']}.tar.gz"
        out = os.path.join(a.out, name)
        write_tar(stage, out)
        if os.path.getsize(out) > MAX_PACKAGE:
            die(f"包太大（{os.path.getsize(out)} 字节，最大 {MAX_PACKAGE}）")
        print(json.dumps({"id": e["id"], "package": name, "sha256": sha256(out)}))
    finally:
        shutil.rmtree(work, ignore_errors=True)


# v1 进程插件现在能用的（和 gTerminal 的 GTPluginManifest.processV1* 是同一份）：别的写了，gTerminal 不收这个条目
V1_POINTS = {"settings", "island.pages", "commands", "drawer.rows", "events"}


def v1_problems(m):
    out = []
    for k, v in (m.get("contributes") or {}).items():
        if v and k not in V1_POINTS:
            out.append(f"贡献点 {k} v1 进程插件还不能用")
    if len((m.get("contributes") or {}).get("island.pages") or []) > 1:
        out.append("v1 进程插件只能有一个岛页（别的页用子路径）")
    for p in m.get("permissions") or []:
        if p != "agent.events:quota" and not p.startswith("net:"):
            out.append(f"权限 {p} v1 进程插件还不能用")
    return out


def safe_extract(tar_bytes, dest):
    """解开 git archive：不许绝对路径和 ..，符号链接不许指到包外（老版本 Python 没有 filter="data"，自己查）"""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as t:
        for m in t.getmembers():
            if m.name.startswith("/") or ".." in m.name.split("/"):
                die(f"仓库里有不安全的路径：{m.name}")
            if m.issym() and (m.linkname.startswith("/") or ".." in m.linkname.split("/")):
                die(f"仓库里的符号链接指到外面：{m.name} → {m.linkname}")
        t.extractall(dest)


def write_tar(stage, out):
    """可复现的 tar.gz：按路径排序、时间 0、属主 0，权限只留可执行位"""
    files = []
    for d, dirs, names in os.walk(stage):
        dirs.sort()
        for n in sorted(names):
            files.append(os.path.join(d, n))
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as t:
        for p in sorted(files):
            rel = os.path.relpath(p, stage)
            ti = t.gettarinfo(p, arcname=rel)
            ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = 0, 0, 0, "", ""
            if ti.issym():
                t.addfile(ti)
                continue
            ti.mode = 0o755 if os.access(p, os.X_OK) else 0o644
            with open(p, "rb") as f:
                t.addfile(ti, f)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------- catalog ----------------

def manifest_from_package(path):
    with tarfile.open(path, "r:gz") as t:
        f = t.extractfile("gterminal-plugin.json")
        if f is None:
            die(f"{path} 的根上没有 gterminal-plugin.json")
        return json.load(f)


def cmd_catalog(a):
    prev = read_previous(a.previous)
    out = []
    for e in entries():
        name = f"{e['id']}-{e['version']}.tar.gz"
        pkg = os.path.join(a.dist, name)
        if os.path.exists(pkg):
            # 这次编的：sha256、大小、清单都从包里重算（不信构建任务交来的数）
            m = manifest_from_package(pkg)
            item = {"package": {"url": f"{a.base_url.rstrip('/')}/{name}", "sha256": sha256(pkg), "size": os.path.getsize(pkg)}, "manifest": m}
        elif not needs_build(e, prev):
            p = prev[e["id"]]
            item = {"package": p["package"], "manifest": p["manifest"]}
        else:
            die(f"{e['id']} 的版本或 commit 变了，但这次没编出包")
        if item["manifest"].get("id") != e["id"] or item["manifest"].get("version") != e["version"]:
            die(f"{e['id']}：包里的清单和条目对不上")
        entry = {
            "id": e["id"], "name": e["name"], "summary": e.get("summary", ""), "license": e["license"], "version": e["version"],
            "source": {"repo": e["source"]["repo"], "commit": e["source"]["commit"], **({"path": e["source"]["path"]} if e["source"].get("path") else {})},
            **item,
        }
        if e.get("withdrawn"):
            entry["withdrawn"] = e["withdrawn"]
        out.append(entry)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"catalog": 1, "plugins": out}, f, ensure_ascii=False, indent=1)
    print(f"写好了 {a.out}：{len(out)} 个插件")


ALLOW_LOCAL = False


def main():
    global ALLOW_LOCAL
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-local", action="store_true", help="本机测试：source.repo 不限于 github.com")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--previous")
    p = sub.add_parser("package")
    p.add_argument("entry")
    p.add_argument("--out", required=True)
    p.add_argument("--clone-from", help="本机测试：从这个本地路径拉源码（代替 source.repo）")
    p = sub.add_parser("catalog")
    p.add_argument("--previous")
    p.add_argument("--dist", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    ALLOW_LOCAL = a.allow_local
    {"plan": cmd_plan, "package": cmd_package, "catalog": cmd_catalog}[a.cmd](a)


if __name__ == "__main__":
    main()
