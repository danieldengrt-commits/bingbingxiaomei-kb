#!/usr/bin/env python3
"""
冰冰小美知识库 部署脚本（SSH 密钥认证）
用法:
  python3 upload.sh          # 完整部署（首次）
  python3 upload.sh --code   # 仅上传代码（日常更新，跳过 chromadb）
"""
import subprocess
import os
import sys

SERVER = "ubuntu@YOUR_SERVER_IP"
REMOTE = "/opt/bingbingxiaomei-kb"
BASE_DIR = os.path.expanduser("~/bingbingxiaomei-kb")
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")
SSH_OPTS = f"-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -i {SSH_KEY}"

CODE_ONLY = "--code" in sys.argv


def ssh(cmd):
    """在服务器上执行命令"""
    full = f"ssh {SSH_OPTS} {SERVER} {cmd!r}"
    print(f"  执行: {cmd}")
    result = subprocess.run(full, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  错误: {result.stderr[:300] if result.stderr else '未知'}")
    else:
        if result.stdout.strip():
            print(result.stdout.strip()[:500])
    return result.returncode == 0


def scp(local_path, remote_path):
    """上传文件到服务器"""
    full = f"scp {SSH_OPTS} {local_path} {SERVER}:{remote_path}"
    print(f"  上传: {local_path} -> {remote_path}")
    result = subprocess.run(full, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  上传失败: {result.stderr[:300]}")
    return result.returncode == 0


print("=" * 50)
print("冰冰小美知识库 - 部署到生产服务器")
print("=" * 50)

# Step 1: 打包项目
print("\n1. 打包项目文件...")
os.chdir(BASE_DIR)

exclude_parts = ["--exclude=vault", "--exclude=__pycache__", "--exclude='*.pyc'",
                 "--exclude=.DS_Store", "--exclude=tests", "--exclude=backup-*",
                 "--exclude=site", "--exclude=research", "--exclude=skills"]
if CODE_ONLY:
    exclude_parts.append("--exclude='data/rag_db'")
    print("  模式: 仅代码（跳过 ChromaDB）")
else:
    print("  模式: 完整部署（含 ChromaDB 向量库）")

exclude_str = " ".join(exclude_parts)
os.system(f"tar czf /tmp/kb-deploy.tar.gz {exclude_str} app.py requirements.txt templates/ static/ src/ data/ 2>/dev/null")
size = os.path.getsize("/tmp/kb-deploy.tar.gz") / 1024 / 1024
print(f"  打包完成: {size:.1f} MB")

# Step 2: 上传
print("\n2. 上传到服务器...")
if not scp("/tmp/kb-deploy.tar.gz", "/tmp/kb-deploy.tar.gz"):
    sys.exit(1)

# Step 3: 解压
print(f"\n3. 解压到 {REMOTE}...")
if not ssh(f"sudo mkdir -p {REMOTE} && sudo tar xzf /tmp/kb-deploy.tar.gz -C {REMOTE} && sudo chown -R ubuntu:ubuntu {REMOTE}"):
    sys.exit(1)

# Step 4: 安装依赖
print("\n4. 检查 Python 环境...")
ssh(f"cd {REMOTE} && python3 -m venv venv && venv/bin/pip install -q -r requirements.txt")

# Step 5: systemd 服务（通过本地文件 scp 上传，避免 echo 转义问题）
print("\n5. 配置 systemd 服务...")
service_config = (
    "[Unit]\n"
    "Description=冰冰小美知识库\n"
    "After=network.target\n\n"
    "[Service]\n"
    "User=ubuntu\n"
    f"WorkingDirectory={REMOTE}\n"
    "Environment=\"DEEPSEEK_API_KEY=your-deepseek-api-key\"\n"
    "Environment=\"FLASK_SECRET_KEY=your-flask-secret-key\"\n"
    f"ExecStart={REMOTE}/venv/bin/gunicorn -w 1 -b 127.0.0.1:5004 --timeout 300 app:app\n"
    "Restart=always\n"
    "RestartSec=5\n\n"
    "[Install]\n"
    "WantedBy=multi-user.target"
)
with open("/tmp/kb-service.service", "w") as f:
    f.write(service_config)
scp("/tmp/kb-service.service", "/tmp/kb-service.service")
ssh("sudo mv /tmp/kb-service.service /etc/systemd/system/bingbingxiaomei-kb.service")

# Step 6: 重启服务
print("\n6. 重启服务...")
ssh("sudo systemctl daemon-reload && sudo systemctl enable bingbingxiaomei-kb && sudo systemctl restart bingbingxiaomei-kb")

# Step 7: 验证
print("\n7. 验证服务状态...")
ssh("sleep 2 && sudo systemctl status bingbingxiaomei-kb --no-pager -l | head -15")

print("\n" + "=" * 50)
print("部署完成!")
print(f"服务端口: 127.0.0.1:5004")
print("=" * 50)
