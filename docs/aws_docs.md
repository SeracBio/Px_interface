# Connecting to AWS via the CLI

How to authenticate the AWS CLI to your AWS account from a terminal.

## 1. Install the AWS CLI

```bash
# Linux / WSL
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

## 2. Get your credentials

In the AWS Console → **IAM** → your user → **Security credentials** → **Create access key**.
You'll receive an **Access Key ID** and a **Secret Access Key**.

## 3. Configure the CLI

```bash
aws configure
```

Prompts:

```
AWS Access Key ID     [None]: AKIA...
AWS Secret Access Key [None]: ****
Default region name   [None]: us-east-1     # or your region
Default output format [None]: json
```

This writes `~/.aws/credentials` and `~/.aws/config`.

## 4. Verify the connection

```bash
aws sts get-caller-identity
```

If it returns your account ID, user ARN, and user ID, you're connected.

## Notes

- **Prefer SSO / `aws configure sso`** if your org uses AWS IAM Identity Center — it avoids
  long-lived keys. Run `aws configure sso` instead of step 3 and follow the browser login.
- **Named profiles** for multiple accounts: `aws configure --profile seracbio`, then use
  `aws s3 ls --profile seracbio` or `export AWS_PROFILE=seracbio`.
- **Security:** never commit `~/.aws/credentials` or paste keys into chats or web tools.
