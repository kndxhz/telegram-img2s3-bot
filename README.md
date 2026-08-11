# telegram-img2s3-bot
这是一个使用Python编写的Telegram机器人，用于将用户发送的图片上传到S3存储服务，并返回图片的公共访问链接。

# 部署
1. 克隆仓库：
    ```bash
    git clone https://github.com/kndxhz/telegram-img2s3-bot
    cd telegram-img2s3-bot
    ```
2. 安装依赖：
    ```bash
    uv sync
    uv venv
    ```
3. 配置环境变量：
   - 复制`.env_example`为`.env`：
    ```bash
    cp .env_example .env
    ```
    - 编辑`.env`文件，填写你的Telegram机器人Token、Socks5代理地址、聊天ID以及S3存储服务的相关信息。
4. 运行机器人：
    ```bash
    uv run main.py
    ```

# 协议
[GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html)