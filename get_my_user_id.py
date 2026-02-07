import requests

# 飞书应用凭证
APP_ID = "cli_a90c958a99b89cd6"
APP_SECRET = "5vo1u6FJj1E68d8Ox1EOOggbsYoeZLbV"

# 1. 获取 tenant_access_token
def get_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    resp = requests.post(url, json=payload)
    data = resp.json()
    if data.get("code") == 0:
        return data["tenant_access_token"]
    else:
        print(f"获取 token 失败: {data}")
        return None

# 2. 获取当前企业的用户列表（需要管理员权限）
def list_users(token):
    url = "https://open.feishu.cn/open-apis/contact/v3/users?page_size=50&user_id_type=user_id"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    data = resp.json()

    if data.get("code") == 0:
        users = data.get("data", {}).get("items", [])
        print(f"\n找到 {len(users)} 个用户：\n")
        for user in users:
            print(f"姓名: {user.get('name')}")
            print(f"User ID: {user.get('user_id')}")
            print(f"邮箱: {user.get('enterprise_email', 'N/A')}")
            print("-" * 50)
    else:
        print(f"\n获取用户列表失败: {data}")
        print("\n如果提示权限不足，请使用以下方法：")
        print("1. 访问 https://feishu.cn/admin")
        print("2. 通讯录 → 成员与部门 → 找到你的账号")
        print("3. 复制 User ID（ou_xxxxx）")

if __name__ == "__main__":
    print("正在获取 User ID...\n")
    token = get_token()
    if token:
        print(f"✅ Token 获取成功")
        list_users(token)
