# DockerGPT Memory on AWS

One t3.large in one region runs the whole stack behind Caddy at `https://<ip-with-dashes>.sslip.io`.
It costs about USD 0.09/h on demand (ap-south-1); **stop it when you're not demoing** (teardown at the end).

Region: `ap-south-1` (Mumbai). Repo: `https://github.com/RamanAsolekar/hacktoberfest`, branch `main`.

## 1. Launch the instance (console, about 10 minutes)

1. EC2 → Launch instance:
   - Name `dockergpt-memory`, AMI **Ubuntu Server 24.04 LTS** (x86_64).
   - Type **t3.large** (2 vCPU, 8 GB). The t2.micro and t3.micro have only 1 GB of RAM, too little for Temporal + Qdrant + Postgres.
   - New key pair `dockergpt-key` (.pem). Keep it outside the repo: `chmod 400 dockergpt-key.pem`.
   - Storage **30 GB gp3**.
2. Security group `dockergpt-sg`, inbound rules only:

   | Port | Source | Why |
   | --- | --- | --- |
   | 22 | each team IP `/32` | SSH |
   | 80 | `0.0.0.0/0` | Let's Encrypt challenge + redirect |
   | 443 | `0.0.0.0/0` | the public app |
   | 8233 | each team IP `/32` | Temporal UI |

   Nothing else: Postgres (5433), Qdrant (6333), Temporal (7233) and the API (8000) stay closed.
3. EC2 → Elastic IPs → Allocate → Associate with the instance. Post the IP in team chat.
   Public host = the IP with dashes + `.sslip.io`, e.g. `13.232.1.2` → `13-232-1-2.sslip.io`.
4. Billing → Budgets → Create budget → monthly cost budget **USD 10**, email alert at 80% to the team.

The same with the AWS CLI (optional; fill in your IPs):

```bash
REGION=ap-south-1
aws ec2 create-key-pair --region $REGION --key-name dockergpt-key --query KeyMaterial --output text > dockergpt-key.pem
SG=$(aws ec2 create-security-group --region $REGION --group-name dockergpt-sg --description "DockerGPT Memory" --query GroupId --output text)
for ip in 203.0.113.10/32 203.0.113.11/32; do   # team IPs
  aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG --protocol tcp --port 22   --cidr $ip
  aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG --protocol tcp --port 8233 --cidr $ip
done
aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG --protocol tcp --port 80  --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --region $REGION --group-id $SG --protocol tcp --port 443 --cidr 0.0.0.0/0
AMI=$(aws ssm get-parameter --region $REGION --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
IID=$(aws ec2 run-instances --region $REGION --image-id $AMI --instance-type t3.large --key-name dockergpt-key \
  --security-group-ids $SG --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=dockergpt-memory}]' --query 'Instances[0].InstanceId' --output text)
ALLOC=$(aws ec2 allocate-address --region $REGION --query AllocationId --output text)
aws ec2 wait instance-running --region $REGION --instance-ids $IID
aws ec2 associate-address --region $REGION --instance-id $IID --allocation-id $ALLOC
```

## 2. Install Docker (over SSH)

```bash
ssh -i dockergpt-key.pem ubuntu@<elastic-ip>
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
exit                                   # log in again so the group applies
ssh -i dockergpt-key.pem ubuntu@<elastic-ip>
docker run --rm hello-world            # done when this prints "Hello from Docker!"
```

## 3. Get the code and fill in .env

```bash
git clone https://github.com/RamanAsolekar/hacktoberfest.git ~/hacktoberfest
cd ~/hacktoberfest
cp .env.example .env
nano .env
```

Set these in `.env` on the server (secrets come from team chat, never from git):

| Variable | Value on AWS |
| --- | --- |
| `POSTGRES_PASSWORD` | a new random password (`openssl rand -hex 16`); use the same one inside `DATABASE_URL` |
| `DEMO_ROOT` | `/home/ubuntu/hacktoberfest/demo` |
| `PUBLIC_HOST` | `<ip-with-dashes>.sslip.io` |
| `PUBLIC_WEB_URL` | `https://<ip-with-dashes>.sslip.io` |
| `NEXT_PUBLIC_API_URL` | *(empty: the browser calls `/api` on the same origin)* |
| `NEXT_PUBLIC_TEMPORAL_UI_URL` | `http://<elastic-ip>:8233` |
| `LLM_*`, `EMBED_*` | from Person 1 (JarvisLabs) |
| `SLACK_WEBHOOK_URL` | from Person 4 |
| `KEPLER_CLI`, `KEPLER_HOME`, `NUDGEBEE_DB_URL` | empty (Kepler is a desktop app; the containers report it as skipped) |

## 4. Start it

```bash
cd ~/hacktoberfest
docker compose up -d postgres qdrant temporal temporal-ui   # infra first (Checkpoint 1)
docker compose ps                                           # all "healthy"
docker compose --profile aws up -d --build                  # everything: api, worker, watcher, web, caddy
docker compose ps
```

## 5. Check health

```bash
curl -s https://<ip-with-dashes>.sslip.io/health            # {"ok":true}
curl -s https://<ip-with-dashes>.sslip.io/api/agent/status  # temporal, qdrant, llm, embed: ok
curl -s -X POST https://<ip-with-dashes>.sslip.io/api/memory/seed
docker compose logs -f worker watcher                       # keep open during the demo
```

The Temporal UI is at `http://<elastic-ip>:8233` (team IPs only).
If Caddy can't get a certificate: check that port 80 is open to `0.0.0.0/0` and run `docker compose logs caddy`.

## 6. Demo run

```bash
cd ~/hacktoberfest
./demo/stack-a/reset.sh && ./demo/stack-b/reset.sh          # clean state
./demo/stack-a/break.sh                                     # "fe_sendauth: no password supplied"
#   approve in the web app or Slack, then:
./demo/stack-b/break.sh                                     # "SASL: ... client password must be a string"
docker compose run --rm --no-deps -v ./eval:/app/eval api python -m eval.bench   # optional: refresh eval/results.md
```

## 7. Update after a push

```bash
cd ~/hacktoberfest && git pull --rebase
docker compose --profile aws up -d --build
```

## 8. Stop and tear down

Pause between sessions (the Elastic IP stays attached, so the URL survives):

```bash
aws ec2 stop-instances --region ap-south-1 --instance-ids <instance-id>   # or EC2 console → Stop
```

After the event (an Elastic IP that isn't attached to a running instance is billed):

1. EC2 → Instances → **Terminate** `dockergpt-memory`.
2. EC2 → Elastic IPs → **Disassociate**, then **Release** the address.
3. EC2 → Volumes → delete any volume left in the `available` state.
4. Delete the key pair and the `dockergpt-sg` security group; keep or delete the budget.

```bash
aws ec2 terminate-instances --region ap-south-1 --instance-ids <instance-id>
aws ec2 release-address    --region ap-south-1 --allocation-id <eipalloc-id>
aws ec2 delete-security-group --region ap-south-1 --group-id <sg-id>
aws ec2 delete-key-pair    --region ap-south-1 --key-name dockergpt-key
```

## Commands actually run

Append the real commands and outputs here as you go (instance id, IP, anything that broke).

- 
