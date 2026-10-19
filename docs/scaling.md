# Auto scaling

Auto scaling adds workers when there's work waiting and lets them go when there isn't, the way a cloud's auto
scaling groups do, within a budget you set. A **provider** is where units launch, a **scaling group** says what each
unit is, how many there may be and the rules that pick the number, and a **unit** is one machine or process the group
launched. Everything it does is in the [activity ledger](activity.md), and its cost counts against the group's
[budget](budgets.md).

Status: in progress. Built: groups, rules, approvals, budgets, the scaler, the API, MCP and assistant tools,
Settings › Auto scaling (the switch, [adding providers and making groups](#in-settings), and each group's terms to
approve or pause), and **This machine**, a provider that runs more `lens worker` processes where Lens runs. In
progress: **AWS EC2** (spot or on demand), **DigitalOcean**, **Hetzner Cloud** and **Lambda** GPU instance providers are built (credentials
checked, prices, launching, watching and deleting machines), and their machines join over the [mesh](mesh.md) as
worker nodes that transcribe and tell voices apart; so are **Proxmox VE**, clones of a VM template on your own server,
and **Linux hosts over SSH**, workers started on machines you have. They haven't been tried on a real cloud machine
yet. Also in progress: **RunPod** GPUs, **Hugging Face** Inference Endpoints and **Amazon SageMaker** real-time
endpoints and **Lambda** GPU instances, which serve a model with vLLM, and **Amazon Bedrock** provisioned throughput;
each takes Lens's model calls while one runs.

Auto scaling is **off** until an admin turns it on, and a group launches nothing until a person approves its terms
signed in to the web app. Agents can propose and manage groups, but never approve one.

## Turn it on

**Settings › Auto scaling** turns it on, or:

```http
PUT /api/v1/settings/scaling
{"enabled": true}
```

| Setting | Default | |
| --- | --- | --- |
| `enabled` | `false` | the master switch: off, every unit is let go and nothing launches |
| `tick_seconds` | `30` | how often the scaler looks (10 to 600) |
| `join_minutes` | `15` | how long a new unit may take to join before it counts as failed (1 to 120) |
| `accrue_minutes` | `5` | how often a running unit's cost goes into the ledger (1 to 60) |
| `tag` | `lens` | the prefix of every unit's name, so they can be told apart at the provider |

One process runs the scaler at a time: the API and every `lens worker` (except those started with `--steps` or
`--no-schedule`, as scaled workers are) take turns holding its lease. While scaling is off and nothing is left to let
go, the scaler doesn't even take the lease.

## In Settings

**Settings › Auto scaling** lists the providers, each with its kind, which credentials are set and whether its last
check passed (and why not, in words when it failed), with **Check again** and **Remove**. Remove takes it out of the
list at once with **Undo** on the notice; it's deleted a few seconds later, or as you leave the page, since its sealed
credentials can't be put back once it's gone. A provider a group uses can't be removed until the group goes.

**Add a provider** asks for the kind (each with what it does), then a form drawn from the driver's `fields`: defaults
filled in, the name first and focused, credentials as password fields (sent as `secrets`, never shown again), lists
one per line, on and off settings as checkboxes, and Enter saves. A driver that needs packages says which, and their
licence, before it fetches them on the first save. The check runs as it's saved; when it fails, the dialog says why.

**New group** on a provider asks for a name (`<provider> workers`), the minimum (0) and maximum (1), a monthly budget
in USD only when the provider charges money (then it's required), and the driver's `template` with its defaults. A
group made here is approved as it's made. Its rules are set by the assistant, MCP or the API for now; without rules it
keeps its minimum running.

## Providers

```http
POST /api/v1/scaling/providers
{"name": "Here", "driver": "local"}
```

`GET /api/v1/scaling/drivers` lists the kinds of provider, with the settings each asks for (`fields`) and what a group
picks for each unit (`template`). A provider's credentials (`secrets`) are sealed when saved, bound to that provider
and field, and opened only for a call to it; the API shows only whether each one is set. Saving a provider proves its
credentials with a read-only call (`check`), kept on the provider; `POST /scaling/providers/{id}/check` runs it again.
A driver that needs a library fetches it the first time a provider of its kind is saved, so the default install
carries none of them.

| Driver | Units | Costs | Template |
| --- | --- | --- | --- |
| `local` (This machine) | more `lens worker` processes where the scaler runs | nothing | `steps`: the steps they run (none: every step) |
| `aws` (AWS EC2) | EC2 instances that join over the mesh and run `lens worker` | the spot or on-demand price, an estimate | `instance_type`, `market` (`spot` or `on-demand`), `max_price`, `fallback`, `image`, `disk_gb`, `steps` |
| `digitalocean` | droplets in `region`, with `token` (sealed), optional `ssh_keys` and `vpc_uuid` | DigitalOcean's hourly list price | `size` (`s-1vcpu-2gb`), `image` (`ubuntu-24-04-x64`), `steps` |
| `proxmox` (Proxmox VE) | clones of a VM template on `node`, at `url` with an API token (`token_id`, `token_secret` sealed); `verify_tls` (on) and `ca_cert` for a private CA | nothing, unless you give it a price | `vm_template` (its id), `full_clone` (off: linked), `storage`, `pool`, `steps` |
| `huggingface` (Hugging Face, a model host) | Inference Endpoints serving `model` with vLLM, with `token` (sealed, one that may manage endpoints); `namespace` (the user or organization; none: the token's user) | the hardware's `pricePerHour` in Hugging Face's list | `model`, `hardware` (`aws-us-east-1-nvidia-l4-x1`), `max_model_len`, `image` (vLLM's), `args`, `use_as_provider` (on) |
| `sagemaker` (Amazon SageMaker, a model host) | real-time endpoints serving `model` with AWS's vLLM container, in `region` with `access_key_id` and `secret_access_key` (sealed) and `role_arn` (the execution role SageMaker runs them as); `hf_token` (sealed) for gated models | the instance type's hosting price in the AWS Price List, an estimate | `model`, `instance_type` (`ml.g5.xlarge`), `max_model_len`, `image` (AWS's vLLM container), `env`, `use_as_provider` (on) |
| `bedrock` (Amazon Bedrock, a model host) | provisioned throughput of `model`, in `region` with `access_key_id` and `secret_access_key` (sealed) | nothing: give the provider Bedrock's price per unit-hour (one unit's `model_units`), or it launches nothing | `model` (a model id or ARN that takes provisioned throughput, or your custom or imported model's ARN), `model_units` (1), `use_as_provider` (on) |
| `runpod` (RunPod, a model host) | GPU pods serving `model` with vLLM, with `token` (sealed); `cloud_type` (`SECURE` or `COMMUNITY`), `data_centers`, `hf_token` (sealed) for gated models | what RunPod charges for the pod it found | `model`, `gpu_types` (`NVIDIA L4`), `gpu_count` (1), `disk_gb` (60), `max_model_len`, `interruptible` (spot, off), `image` (vLLM's), `args`, `use_as_provider` (on) |
| `ssh` (Linux hosts over SSH) | `lens worker` started on `hosts` (`user@host` or `user@host:port`), signed in with `private_key` (sealed; `passphrase` if it has one) | nothing, unless you give it a price | `per_host` (1): workers on each host; `steps` |
| `hetzner` (Hetzner Cloud) | servers in `location`, with `token` (sealed), optional `ssh_keys` | Hetzner's hourly gross price in euros, times `usd_per_eur` (1.1; set your rate) | `server_type` (`cx22`), `image` (`ubuntu-24.04`), `steps` |
| `lambda` (Lambda, GPU instances) | instances in `region` (`us-east-1`), with `token` (a Lambda Cloud API key, sealed) and `ssh_key` (the name of a key in your Lambda account; none: its first) | Lambda's `price_cents_per_hour` for the type | `instance_type` (`gpu_1x_a10`), `steps` |
| `lambda-models` (Lambda, a model host) | GPU instances serving `model` with vLLM over HTTPS, in `region` with `token` (sealed) and `ssh_key` as `lambda`; `hf_token` (sealed) for gated models | Lambda's `price_cents_per_hour` for the type | `model`, `instance_type` (`gpu_1x_a10`), `max_model_len`, `image` (vLLM's), `args`, `use_as_provider` (on) |

A worker on This machine stops with the process that started it, and the scaler that takes over starts them again where
it runs.

### Units Lens lost track of

While scaling runs, the scaler asks each provider every ten minutes for the units it runs that carry Lens's tag (the
driver's `listing`, a read-only call in the activity ledger). One that no unit of Lens's stands for is a stray: a launch
that started a machine but whose answer never came back, say, or a machine someone made by hand with Lens's tag. A
stray may cost money, so it shows on its provider (`strays` in `GET /api/v1/scaling/providers`, with when Lens first
saw it, and in Settings › Auto scaling) and goes in the activity ledger once, as `scaling.stray`. Lens lets none of them
go itself, since it may not have started them: a person signed in to the web app does, with Let go in Settings (Undo
stays a few seconds) or `POST /api/v1/scaling/providers/{id}/strays/let-go` with the stray's `id`, which terminates it
at the provider. An agent asks for that (`let_go_scaling_stray` in the assistant), and the ask waits for the person's
yes on the Approvals page; an API key or an app gets 403. A unit Lens let go within the last hour isn't counted, since a provider may
still show it while it goes. A unit of any of Lens's providers counts, so two providers on one account (Lambda's
workers and its model hosts) don't take each other's units for strays. When the provider can't be asked, `list_error`
says why.

### Machines elsewhere

A cloud provider's unit is a new machine. Its first boot runs a script (user-data) that sets `LENS_SCALING_UNIT` (its
name) and `LENS_WORKER_STEPS` (the template's steps), then the mesh's install line with a join token made for that
unit, usable once within an hour. The machine joins Lens over the [mesh](mesh.md) as a worker node and starts
`lens worker`; it asks for its own database user over the mesh once it has joined, so the user-data holds no
long-lived secret. When the unit goes (let go, failed, or never came up), it leaves the mesh too: its join token is
withdrawn if unspent, and the node that joined with it is taken off (its certificates on the blocklist, its database
user removed), as removing it on the Nodes page does. A worker node runs only `transcribe` and `diarize` (it never gets the archive's key:
[Worker nodes](mesh.md#worker-nodes)), so a group on these providers counts only those steps' jobs in its metrics
(`unit_steps`), and one whose template names none of them waits and launches nothing. Each machine
carries Lens's tag (`scaling.tag`: a DigitalOcean tag, a Hetzner label `lens=<tag>`, a Lambda tag `lens` with the tag
as its value and `lens-unit` with the unit's name), so Lens's machines can be told apart. Letting a unit go deletes its
machine; a machine switched off at the provider counts as gone and is deleted.

A Lambda unit is a GPU instance of `instance_type`, so its worker transcribes and tells voices apart on the GPU. Lambda
puts an SSH key on every instance, so the account needs one (the provider's check says so when it has none); Lens
never signs in with it. The check counts the instance types with room in the region; a launch where Lambda has none
fails with Lambda's message, which counts toward pausing the group. An instance Lambda takes back (`preempted`)
counts as gone, with that in the unit's `why`.

A Proxmox unit is a clone of `vm_template`, tagged with Lens's tag and started. Its template needs the QEMU guest
agent (`qemu-guest-agent` installed, and the VM's Guest Agent option on): once the agent answers, Lens runs the same
first-boot script in the clone through it, then tags it `lens-booted`, so nothing is put on the server's storage. The
script stays in Lens's memory until then; a clone whose boot was cut short, by Lens restarting, never joins and is
deleted. Letting a unit go stops the clone and deletes it with its disks. The API token needs to clone, configure,
start, stop and delete VMs and use their guest agent (the `PVEVMAdmin` role on the template and the clones' pool is
enough).

An SSH unit is the first-boot script started on one of the provider's hosts, the first with fewer than `per_host` of
Lens's workers running, in its own process group (`setsid`) under `~/.lens-scaling/`: the script is written there
readable only by that user and deletes itself as it starts, since it holds the unit's join token; the unit's process
id and log stay beside it. Letting a unit go stops its process group and removes its files. Host keys are trusted on
first use: saving or checking the provider records each host's key on it (`check.host_keys`, kept when a later check
fails), and every other call refuses a host whose key differs. A host whose key changed for a good reason is taken off
the provider and added back. The SSH client is Paramiko (LGPL-2.1, used unmodified), the `ssh` extra, fetched the
first time an SSH provider is saved.

Until the mesh is on (Settings › Network), a group on these providers waits (`waiting_for` says so) and launches
nothing, since its machines couldn't join. They call the providers' REST APIs with httpx, so they install nothing.

### Model hosts

A model host's unit is a model server, not a worker, so its group is a `model` group and needs nothing from the mesh.
A RunPod unit is a pod running vLLM's OpenAI-compatible server for the template's `model`, on the first of
`gpu_types` RunPod has free, its port 8000 behind RunPod's HTTPS proxy (`https://<pod>-8000.proxy.runpod.net/v1`).
Lens makes an API key for each unit: it goes to vLLM in the pod's environment (`VLLM_API_KEY`, which RunPod keeps
with the pod, as it does `HF_TOKEN`), and Lens keeps it sealed on the unit; the API shows only that it is set. The
unit is running once vLLM answers its health check, after the weights are downloaded and loaded, so `join_minutes`
has to leave room for a big model. A pod stopped at RunPod (a spot pod taken back) counts as gone and is deleted,
since a stopped pod's disk still costs.

A Hugging Face unit is an Inference Endpoint serving the template's `model` with vLLM on `hardware`, an id from
Hugging Face's hardware list that names the cloud, region, GPU and how many (`aws-us-east-1-nvidia-l4-x4`: four L4s
in AWS us-east-1, so vLLM splits the model across four). Lens reads the price, the cloud and the instance from that
list, and makes one endpoint per unit with one replica (Lens scales by units). The endpoint is public at Hugging
Face's address, and vLLM asks for the unit's own key, which goes to the endpoint as an encrypted secret
(`VLLM_API_KEY`); Lens's model calls carry that key, never the Hugging Face token. The unit is running once Hugging
Face says the endpoint runs. One that fails, is paused or scaled to zero at Hugging Face counts as gone and is
deleted, with Hugging Face's reason (a gated model it may not download, say) in the unit's `why`.

A Lambda model unit is a GPU instance whose first boot runs vLLM's image with Docker (which Lambda's image comes with),
serving the template's `model` over HTTPS on port 8000, split across the instance's GPUs (`gpu_8x_...` runs eight
tensor-parallel shards). Lambda gives an instance no name a public certificate authority would vouch for, so each
group has a certificate authority of its own: Lens makes it once, keeps its key sealed on the group, and signs a
certificate for each unit, which goes to the instance in its first-boot script with the unit's key. While the unit
serves, `llm.tls_ca` holds the group's certificate, and Lens's calls trust only a certificate it signed. A firewall
ruleset Lens makes once per region (`<tag>-models`) opens port 8000 on the instances launched with it; vLLM asks every
call for the group's key. The unit is running once vLLM answers its health check over HTTPS.

A SageMaker unit is a real-time endpoint in your AWS account serving the template's `model` with AWS's vLLM container
for SageMaker on one `instance_type` instance. It is three things at SageMaker, a model, an endpoint configuration and
the endpoint, all named after the unit and tagged `lens-unit` with its name and `lens` with Lens's tag; terminating a
unit deletes all three. The container's `SM_VLLM_*` settings are vLLM's options: Lens sets the model, its served name,
the context length and, from the Price List, one tensor-parallel shard per GPU of the instance; `env` adds more as
`NAME=value` (`SM_VLLM_QUANTIZATION=awq`). The unit is running once SageMaker says the endpoint is in service; one that
fails or goes out of service counts as gone, with SageMaker's `FailureReason` in the unit's `why`.

SageMaker endpoints take only calls signed with an AWS key, so while a SageMaker unit serves Lens's model calls, the
LLM provider's address is the endpoint's (`https://runtime.sagemaker.<region>.amazonaws.com/endpoints/<name>`) and
its key is the provider's AWS key as `<access key id>:<secret access key>`. Lens posts chat requests to the
endpoint's `/invocations`, signed with AWS Signature Version 4; the secret key itself is never sent. You can also set
such an endpoint as the LLM provider yourself in Settings → LLM provider. Search by meaning and the voice don't use a
SageMaker endpoint for their calls: give them a server of their own.

AWS's vLLM container is published by account `763104351884` in most regions (`vllm:<version>-sagemaker`);
[AWS's list of its containers](https://aws.github.io/deep-learning-containers/reference/available_images/) has the
others. The keys need `sagemaker:ListEndpoints`, `sagemaker:DescribeEndpoint`, `sagemaker:CreateModel`,
`sagemaker:CreateEndpointConfig`, `sagemaker:CreateEndpoint`, `sagemaker:DeleteEndpoint`,
`sagemaker:DeleteEndpointConfig`, `sagemaker:DeleteModel`, `sagemaker:AddTags`, `sagemaker:InvokeEndpoint`,
`sagemaker:InvokeEndpointWithResponseStream`,
`iam:PassRole` on the execution role, and `pricing:GetProducts` for prices you don't give yourself. The execution role
is one SageMaker may assume (the `AmazonSageMakerFullAccess` managed policy, or one that only pulls from ECR and
writes logs).

A Bedrock unit is a provisioned throughput of the template's `model` with `model_units` model units, made with no
commitment, so AWS bills it by the hour until Lens deletes it. It is named after the unit and tagged `lens-unit` with
its name and `lens` with Lens's tag. The unit is running once Bedrock says it is in service; one that fails counts as
gone, with Bedrock's `failureMessage` in the unit's `why`. While it serves, the LLM provider's address is Bedrock's
runtime (`https://bedrock-runtime.<region>.amazonaws.com`), its model the provisioned throughput's ARN and its key the
provider's AWS key; Lens's calls go to Bedrock's Converse API, signed ([LLM provider](configuration.md)). Bedrock
doesn't list provisioned throughput in the AWS Price List, so the provider's price is yours to give, from Bedrock's
pricing page, and no unit launches without it. Which models take provisioned throughput, and in which regions, is
Bedrock's to say; a launch it refuses fails with its message. The keys need
`bedrock:CreateProvisionedModelThroughput`, `bedrock:GetProvisionedModelThroughput`,
`bedrock:ListProvisionedModelThroughputs`, `bedrock:DeleteProvisionedModelThroughput`, `bedrock:TagResource`,
`bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`.

A unit that never comes up, whether it doesn't join within `join_minutes` or its provider reports it failed, counts
as a failed launch: three in a row pause the group (with the reason in its `paused_why`), and a unit that comes up
clears the count.

The tests hold the fakes of Hugging Face, RunPod, DigitalOcean, Hetzner Cloud and Lambda to each provider's own published
API document (trimmed copies in `fastapi_backend/tests/data/openapi/`, each with where and when it was fetched): a
request the real API wouldn't take, an answer it couldn't give, or a field Lens reads that the document doesn't have,
fails the test. AWS runs against [moto](https://github.com/getmoto/moto), which keeps EC2's instances, tags, volumes
and spot markets and SageMaker's models, endpoint configurations and endpoints the way AWS does, so the drivers and the
scaler go from launch to scale-in as they would on a real account; Lens's signed SageMaker calls are held to
botocore's own model of SageMaker's runtime API. moto has no Bedrock provisioned throughput, so botocore's Stubber
answers for Bedrock, holding each request and answer to AWS's model of Bedrock's API and each name Lens sends to the
model's patterns; Lens's Converse calls are held to it the same way.
Proxmox's fake is held the same way to Proxmox's own schema of its API, converted from its API viewer (forms
read by the types the schema gives). SSH hosts have no API document: each test host is a real SSH server (Paramiko's, a
test-only dependency) that checks the client's key and runs the driver's commands in a real shell. A Lambda model
unit's certificate is tried against a real HTTPS server on the test machine: Lens's calls go through with the group's
certificate authority and fail with no `llm.tls_ca` or another group's.

While a model group's unit runs, it is Lens's LLM provider (the template's `use_as_provider`, on by default): the
scaler points Settings › LLM provider at the unit's address, model and key, keeping the provider that was there
sealed, and puts it back once no unit of such a group runs. When that unit goes and another runs, the other takes
over. A provider a person picks meanwhile stays: Lens doesn't point at a unit again until none has run. Each switch
is in the activity ledger (`scaling.serve`, `scaling.serve.end`).

Model calls go to every running unit of the group in turn, not only the one Settings shows: a group's units serve
with one key (made once, sealed on the group), and the scaler keeps `llm.spread` at the shown unit's address and the
others' (`{"base_url": ..., "urls": [...]}`), so a group scaled out to three units takes three times the calls. A
unit that can't be reached hands its call to the next. A spread is used only while Settings still shows the address
it was made for, so a provider a person picks never sends calls to a unit. Units launched before groups had one key
(Lens before this) keep their own and only serve as the shown unit until they are replaced. Bedrock's units are
models at one address rather than addresses, so a Bedrock group serves from one unit at a time.

## AWS EC2

Lens grows onto AWS when work waits: from a Raspberry Pi at home to as many EC2 instances as the group's max and
budget allow, and back to none. Each unit is an instance that joins Lens over the [mesh](mesh.md) and runs its worker.

```http
POST /api/v1/scaling/providers
{"name": "AWS", "driver": "aws", "settings": {"region": "eu-west-1"},
 "secrets": {"access_key_id": "AKIA...", "secret_access_key": "..."}}
```

| Provider setting | Default | |
| --- | --- | --- |
| `region` | `us-east-1` | the region units launch in |
| `access_key_id`, `secret_access_key` | | an IAM user's keys, sealed |
| `subnet_id` | the default VPC's | where units launch |
| `security_group_ids` | the VPC's default group | units only reach out (to the mesh's lighthouse), so they need no inbound rule |
| `key_name` | none | an EC2 key pair, to sign in to units yourself |

| Unit (template) | Default | |
| --- | --- | --- |
| `instance_type` | `t4g.medium` | any EC2 type; Arm (Graviton) and x86 both work |
| `market` | `spot` | `spot`: AWS's spare capacity, often far cheaper, which AWS may take back with two minutes' notice; `on-demand` |
| `max_price` | the on-demand price | the most a spot unit may cost per hour |
| `fallback` | `true` | when AWS has no spot capacity of that type, or none under the max price, launch on demand instead |
| `image` | the latest Ubuntu 24.04 | an AMI; by default Canonical's for the type's architecture, found in the region |
| `disk_gb` | `30` | the unit's disk (gp3, encrypted) |
| `steps` | every step | the steps its worker runs |

**Spot instances.** When AWS takes a spot instance back, the scaler sees it gone, records why ("AWS took the spot
instance back") and launches another, on demand if spot has run out and `fallback` allows. The job its worker was
running goes back in the queue once its heartbeat stops, so nothing is lost.

**What it costs.** Each unit's price is an estimate: the spot price in the zone it landed in (from
`DescribeSpotPriceHistory`), or the on-demand list price (from the AWS Price List API). A price you give the provider
wins over both. Without any price Lens launches nothing, because a unit with no price would never count against the
budget. AWS bills separately; the ledger's figures are estimates.

**Hardened by default.** Every instance requires IMDSv2, has an encrypted disk that goes with it, terminates itself
when it shuts down (a stopped one is terminated too, so its disk doesn't keep costing), and is tagged `lens-unit` with
its name and `lens` with Lens's tag. Its user-data is the one every cloud unit boots with ([machines
elsewhere](#machines-elsewhere)).

The keys need these permissions (an IAM policy for a user only Lens uses):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Resource": "*", "Action": [
      "ec2:DescribeAvailabilityZones", "ec2:DescribeInstanceTypes", "ec2:DescribeImages",
      "ec2:DescribeSpotPriceHistory", "ec2:DescribeInstances", "ec2:RunInstances",
      "ec2:CreateTags", "ec2:TerminateInstances", "pricing:GetProducts"]}
  ]
}
```

`pricing:GetProducts` is only needed for on-demand prices you don't give yourself. Lens talks to AWS through botocore
(Apache-2.0), fetched the first time an AWS provider is saved.

## Groups

```http
POST /api/v1/scaling/groups
{"name": "Transcribers", "provider": 1, "template": {"steps": ["transcribe"]}, "min": 0, "max": 4,
 "rules": [{"type": "step", "metric": "jobs_waiting", "above": 10, "minutes": 2, "add": 2},
           {"type": "step", "metric": "jobs_waiting", "below": 1, "minutes": 10, "remove": "50%"}]}
```

| Field | Default | |
| --- | --- | --- |
| `name`, `provider` | | |
| `template` | the driver's defaults | what each unit is |
| `min`, `max` | `0`, `1` | the bounds, up to 100 |
| `desired` | `min` | units wanted now; the rules change it |
| `cooldown_seconds` | `300` | after scaling out or in, how long before the next change the same way |
| `warmup_seconds` | `300` | how long a launching unit may take to join before more are judged |
| `terminate` | `oldest` | which goes first when scaling in: `oldest` or `newest`. Idle units go before busy ones, protected ones never |
| `rules` | none | up to 20, below |
| `budget` | none | USD a month; needed on a provider that charges |
| `at_cap` | `stop` | at the budget's cap: `stop` every unit, or keep `min` |
| `enabled` | `true` | off, its units are let go |

`PATCH /scaling/groups/{id}` changes one, `POST /scaling/groups/{id}/desired {"desired": 3}` asks for a number now
(within min and max), `POST /scaling/groups/{id}/pause {"paused": true, "why": "…"}` stops new launches while what runs
stays, and `DELETE /scaling/groups/{id}` lets its units go and removes it. `GET /scaling/groups/{id}/history` is what
happened to it, from the ledger. Each group says `waiting_for`: why it launches nothing now, in words.

### Rules

| Type | Fields | Proposes |
| --- | --- | --- |
| `target` | `metric`, `target` | units × metric ÷ target, rounded up: keeps the metric near the target |
| `step` | `metric`, `above` or `below`, `minutes` (default 1), `add` or `remove` (a count or a percentage like `"50%"`) | once the condition has held for `minutes`, that many more or fewer |
| `schedule` | `cron`, `timezone`, and any of `min`, `max`, `desired` | at each time the cron names, the new bounds and count |

When several rules speak, the largest number wins; the result stays within min and max. Each rule comes back with
`says`, the rule in words, and the group's `last_eval` keeps what each said last time.

| Metric | |
| --- | --- |
| `jobs_waiting` | jobs queued (for a group's `steps`, those that run them) |
| `oldest_wait_minutes` | how long the oldest queued job has waited |
| `workers_busy_share` | the share of live workers running a job |
| `worker_load` | the live workers' average load |
| `model_calls_per_minute` | model calls over the last 5 minutes |
| `model_seconds` | how long a model call took on average over the last 5 minutes |

`GET /api/v1/scaling/metrics` gives them now.

## The person approves

A group launches nothing until a person approves its **terms**: the provider, what each unit is, the max, the budget
and what happens at its cap. Settings › Auto scaling lists each group with its terms in words and an **Approve**
button; `POST /scaling/groups/{id}/approve` approves them as they stand, and only from a session signed in to the web
app: an API key, an app or an agent gets 403. A group made or changed by a person in the web app
is approved as it's saved, since that is their pick.

Within the approved terms anyone who may change the group, agents included, acts at once: asking for a number of units,
lowering the max or the budget, pausing, letting a unit go. A change that widens the terms (another provider or unit,
a higher max or budget, keeping min at the cap) takes the approval away until a person gives it again.

## The scaler

Every `tick_seconds`, for each group, the scaler:

1. puts each running unit's cost since last time into the ledger (`scaling.unit`, an estimate from its price per hour);
2. checks the group's budget and the `all` budget: at the cap the group is `capped` and lets its units go (or keeps
   min, with `at_cap: "min"`);
3. looks at each unit at its provider and in Lens. A worker unit is running once its worker beats; one that hasn't
   joined within `join_minutes`, or whose worker has been silent for 10 minutes, fails and is let go;
4. runs the rules and launches or lets go units to reach the desired count. A busy worker drains first: its job goes
   back to the queue after the step it's on, and the unit goes once it's idle.

Three launches or joins that fail in a row pause the group, with the reason. Every launch, join, drain, termination,
failure and change is a ledger row linked to the unit, its group and its provider.

## Agents

The same in the assistant (admins, in the server tools) and over MCP: `scaling` reads everything,
`propose_scaling_group` and `change_scaling_group` make and change groups, `set_scaling_desired`,
`pause_scaling_group` and `terminate_scaling_unit` act within the approved terms. In the assistant, proposing or
changing a group waits for the person's yes; a yes on the Approvals page in the web app also approves the group's
terms, while a group the assistant makes in a setup conversation, or an agent over MCP, still waits for the person.
Turning scaling on (`change_settings scaling`) always asks, and so does letting a stray go (`let_go_scaling_stray`),
which only that yes in the web app does.

## Refine later

Serving only some kinds of call (chat, decisions); more model
hosts; Bedrock's price per unit-hour from AWS and Bedrock API keys as the
provider's key; Hugging Face's scale to zero and private endpoints; RunPod network volumes, so a model is downloaded once; Proxmox LXC containers; a live euro rate;
preemptible and spot instances at clouds other than AWS and RunPod; draining an AWS spot unit on its two-minute notice
(the node agent reading the instance's metadata) and handing its job back at once; following the spot price as it
changes; AWS sign-in with an instance role or IAM Identity Center instead of keys; mixed instance types per group;
warm pools; predictive scaling; health checks beyond
joining; prices fetched from providers' pricing APIs. In Settings: rules, changing a provider or a group, removing a
group, and a provider's price and note.
