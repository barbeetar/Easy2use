# Kubescape + Headlamp Plugin 部署與驗證 SOP

> 環境：Kubernetes v1.36.1  
> Cluster：`k8sstag`  
> Kubescape Operator：`1.40.4`  
> Kubescape Scanner：`v4.0.13`  
> Headlamp：`v0.43.0`  
> Kubescape Headlamp Plugin：`v0.11.2`

---

# 目錄

- [1. 架構說明](#1-架構說明)
- [2. 環境資訊](#2-環境資訊)
- [3. Quick Start](#3-quick-start)
- [4. Helm Repository](#4-helm-repository)
- [5. Kubescape 使用 Image](#5-kubescape-使用-image)
- [6. 搬 Image 至 Private Registry](#6-搬-image-至-private-registry)
- [7. Offline Artifacts](#7-offline-artifacts)
- [8. 建立 Artifacts PVC](#8-建立-artifacts-pvc)
- [9. Loader Pod 上傳 Artifacts](#9-loader-pod-上傳-artifacts)
- [10. 驗證 Offline Artifacts](#10-驗證-offline-artifacts)
- [11. Kubescape Operator Values](#11-kubescape-operator-values)
- [12. 安裝 Kubescape Operator](#12-安裝-kubescape-operator)
- [13. 確認 Kubescape Pods](#13-確認-kubescape-pods)
- [14. 確認 CRD](#14-確認-crd)
- [15. Grype Offline DB](#15-grype-offline-db)
- [16. Configuration Scan 驗證](#16-configuration-scan-驗證)
- [17. Vulnerability Scan 驗證](#17-vulnerability-scan-驗證)
- [18. Kubescape CronJob](#18-kubescape-cronjob)
- [19. 手動從 CronJob 觸發 Scan](#19-手動從-cronjob-觸發-scan)
- [20. Operator API 手動 Framework Scan](#20-operator-api-手動-framework-scan)
- [21. NSA / MITRE / CIS 測試](#21-nsa--mitre--cis-測試)
- [22. Scan Coverage Warning](#22-scan-coverage-warning)
- [23. Storage 與 CRD Persist](#23-storage-與-crd-persist)
- [24. C-0240 Continuous Scan 問題](#24-c-0240-continuous-scan-問題)
- [25. Headlamp Plugin](#25-headlamp-plugin)
- [26. Headlamp Values](#26-headlamp-values)
- [27. 安裝 Kubescape Headlamp Plugin](#27-安裝-kubescape-headlamp-plugin)
- [28. Headlamp Plugin 驗證](#28-headlamp-plugin-驗證)
- [29. Headlamp Compliance 計算方式](#29-headlamp-compliance-計算方式)
- [30. CLI 與 Headlamp Score 差異](#30-cli-與-headlamp-score-差異)
- [31. Namespace Compliance 注意事項](#31-namespace-compliance-注意事項)
- [32. 常用查核指令](#32-常用查核指令)
- [33. Troubleshooting](#33-troubleshooting)
- [34. 最終狀態](#34-最終狀態)

---

# 1. 架構說明

整體架構：

```text
                 Offline Artifacts PVC
                         │
                         ▼
                  Kubescape Scanner
                         │
          ┌──────────────┴──────────────┐
          │                             │
          ▼                             ▼
 Configuration Scan             Vulnerability Scan
          │                             │
          │                         Kubevuln
          │                             │
          │                         Grype DB
          │                             │
          └──────────────┬──────────────┘
                         │
                         ▼
                      Storage
                         │
                         ▼
     spdx.softwarecomposition.kubescape.io
                         │
         ┌───────────────┼────────────────┐
         │               │                │
         ▼               ▼                ▼
 WorkloadConfiguration  Vulnerability   Summary
      Scan CRDs             CRDs          CRDs
         │
         ▼
 Headlamp Kubescape Plugin
         │
         ▼
      Web UI
```

資料流程：

```text
Framework JSON
    ↓
Kubescape Scan
    ↓
Scan Result
    ↓
Storage
    ↓
Kubescape CRD
    ↓
Headlamp Plugin
    ↓
Compliance / Vulnerability UI
```

---

# 2. 環境資訊

## Kubernetes

```text
Kubernetes Version : v1.36.1
Cluster            : k8sstag
Kubescape Namespace: kubescape
Headlamp Namespace : headlamp
```

## Kubescape

```text
Helm Chart   : kubescape/kubescape-operator
Chart Version: 1.40.4
Scanner Image: kubescape:v4.0.13
```

## Headlamp

```text
Release      : headlamp
Namespace    : headlamp
Chart Version: 0.43.0
App Version  : 0.43.0
```

## Private Registry

```text
utcsyquay.unimicron.com
```

Kubescape：

```text
utcsyquay.unimicron.com/helm-charts/kubescape/
```

Headlamp：

```text
utcsyquay.unimicron.com/registryk8s/headlamp
```

Kubescape Headlamp Plugin：

```text
utcsyquay.unimicron.com/helm-charts/headlamp/kubescape-headlamp-plugin:v0.11.2
```

---

# 3. Quick Start

快速確認 Kubescape：

```bash
kubectl get pod -n kubescape
```

確認 Configuration Scan：

```bash
kubectl get workloadconfigurationscansummaries -A
```

確認 Vulnerability Scan：

```bash
kubectl get vulnerabilitymanifestsummaries -A
```

確認 CronJob：

```bash
kubectl get cronjob -n kubescape
```

確認 Framework Scan：

```bash
kubectl logs -n kubescape deploy/kubescape --since=30m \
  | grep -E 'Framework scanned|Done scanning|Scan results saved|Overall compliance-score'
```

確認 Storage Error：

```bash
kubectl logs -n kubescape deploy/kubescape --since=30m \
  | grep -iE 'persist|storage|error|failed'
```

---

# 4. Helm Repository

## Kubescape

加入 Kubescape Helm repository：

```bash
helm repo add kubescape https://kubescape.github.io/helm-charts/
helm repo update
```

確認：

```bash
helm search repo kubescape/kubescape-operator --versions | head
```

確認版本：

```text
1.40.4
```

---

## Headlamp

加入：

```bash
helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/
helm repo update
```

確認：

```bash
helm search repo headlamp/headlamp --versions | head
```

---

# 5. Kubescape 使用 Image

Kubescape Operator `1.40.4` 使用的主要 images：

```text
quay.io/kubescape/http-request:v0.2.23
quay.io/kubescape/kubescape:v4.0.13
quay.io/kubescape/kubevuln:v0.3.430
quay.io/kubescape/node-agent:v0.3.219
quay.io/kubescape/operator:v0.2.169
quay.io/kubescape/storage:v0.0.331
```

安裝前可先確認 Helm Render：

```bash
helm template kubescape \
  kubescape/kubescape-operator \
  --version 1.40.4 \
  -n kubescape \
  -f values-utf8.yaml \
  | grep 'image:' \
  | sort -u
```

---

# 6. 搬 Image 至 Private Registry

## Kubescape Scanner

```bash
skopeo copy \
  docker://quay.io/kubescape/kubescape:v4.0.13 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/kubescape:v4.0.13
```

## HTTP Request

```bash
skopeo copy \
  docker://quay.io/kubescape/http-request:v0.2.23 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/http-request:v0.2.23
```

## Kubevuln

```bash
skopeo copy \
  docker://quay.io/kubescape/kubevuln:v0.3.430 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/kubevuln:v0.3.430
```

## Node Agent

```bash
skopeo copy \
  docker://quay.io/kubescape/node-agent:v0.3.219 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/node-agent:v0.3.219
```

## Operator

```bash
skopeo copy \
  docker://quay.io/kubescape/operator:v0.2.169 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/operator:v0.2.169
```

## Storage

```bash
skopeo copy \
  docker://quay.io/kubescape/storage:v0.0.331 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/storage:v0.0.331
```

---

# 7. Offline Artifacts

由於 Kubernetes 環境無法直接從 GitHub 取得 Kubescape Policy，因此使用 Offline Artifacts。

建立工作目錄：

```bash
mkdir -p ~/kubescape
cd ~/kubescape
```

下載：

```bash
kubescape download artifacts \
  --output ./kubescape-artifacts
```

確認：

```bash
ls -lh ./kubescape-artifacts
```

目前 artifacts：

```text
agentruntimehardening.json
allcontrols.json
armobest.json
attack-tracks.json
cis-aks-t1.2.0.json
cis-aks-t1.8.0.json
cis-eks-t1.7.0.json
cis-eks-t1.8.0.json
cis-gke-v1.9.0.json
cis-v1.10.0.json
cis-v1.12.0.json
controls-inputs.json
devopsbest.json
exceptions.json
mitre.json
nsa.json
soc2.json
```

---

# 8. 建立 Artifacts PVC

原本嘗試使用 ConfigMap，但 artifacts 約 5 MiB，會遇到：

```text
Request entity too large
limit is 3145728
```

因此改用 PVC。

建立：

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: kubescape-artifacts
  namespace: kubescape
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: rook-cephfs
  resources:
    requests:
      storage: 1Gi
```

套用：

```bash
kubectl apply -f kubescape-artifacts-pvc.yaml
```

確認：

```bash
kubectl get pvc -n kubescape
```

---

# 9. Loader Pod 上傳 Artifacts

建立 Loader Pod：

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: kubescape-artifacts-loader
  namespace: kubescape
spec:
  containers:
    - name: loader
      image: busybox:1.36
      command:
        - sh
        - -c
        - sleep 86400
      volumeMounts:
        - name: artifacts
          mountPath: /artifacts
  volumes:
    - name: artifacts
      persistentVolumeClaim:
        claimName: kubescape-artifacts
```

套用：

```bash
kubectl apply -f kubescape-artifacts-loader.yaml
```

確認：

```bash
kubectl get pod -n kubescape kubescape-artifacts-loader
```

上傳：

```bash
kubectl cp \
  ./kubescape-artifacts/. \
  kubescape/kubescape-artifacts-loader:/artifacts
```

查看：

```bash
kubectl exec -n kubescape kubescape-artifacts-loader -- \
  ls -lh /artifacts
```

---

# 10. 驗證 Offline Artifacts

Kubescape Pod 使用：

```text
KS_DOWNLOAD_ARTIFACTS=false
KS_CACHE_DIR=/opt/kubescape-artifacts
```

確認：

```bash
kubectl get deploy kubescape -n kubescape \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' \
  | grep -iE 'ARTIFACT|CACHE|DOWNLOAD|POLICY'
```

應看到：

```text
KS_DOWNLOAD_ARTIFACTS=false
KS_CACHE_DIR=/opt/kubescape-artifacts
```

確認 Mount：

```bash
kubectl get deploy kubescape -n kubescape -o yaml \
  | grep -A12 -B12 '/opt/kubescape-artifacts'
```

應看到：

```yaml
mountPath: /opt/kubescape-artifacts
name: kubescape-artifacts
```

---

# 11. Kubescape Operator Values

## 11.1 建議測試階段 Capabilities

```yaml
capabilities:
  configurationScan: enable

  # 測試階段建議先關閉
  # 避免 C-0240 continuous scan 問題
  continuousScan: disable

  nodeScan: disable
  nodeSbomGeneration: enable
  scanEmbeddedSBOMs: disable

  vulnerabilityScan: enable

  relevancy: disable
  vexGeneration: disable

  runtimeObservability: disable
  networkPolicyService: disable
  networkEventsStreaming: disable
  runtimeDetection: disable
  malwareDetection: disable
  nodeProfileService: disable

  admissionController: disable
  httpDetection: disable
  seccompProfileService: disable

  manageWorkloads: disable
  syncSBOM: disable
  agentRuntimePosture: disable
  riskAcceptance: disable
  remediation: disable
  autoUpgrading: disable

  kubescapeOffline: disable
  prometheusExporter: disable
```

---

## 11.2 Service Scan

```yaml
serviceScanConfig:
  enabled: false
```

---

## 11.3 Offline Artifacts

Kubescape environment：

```yaml
kubescape:
  env:
    - name: KS_DOWNLOAD_ARTIFACTS
      value: "false"

    - name: KS_CACHE_DIR
      value: /opt/kubescape-artifacts
```

PVC：

```yaml
extraVolumes:
  - name: kubescape-artifacts
    persistentVolumeClaim:
      claimName: kubescape-artifacts
```

Mount：

```yaml
extraVolumeMounts:
  - name: kubescape-artifacts
    mountPath: /opt/kubescape-artifacts
```

---

## 11.4 注意 mountPath

不要直接使用：

```text
/home/nonroot/.kubescape
```

因為 Helm Chart 本身已經有：

```text
/home/nonroot/.kubescape
/home/nonroot/.kubescape/host-scanner.yaml
```

否則 Helm 可能出現：

```text
duplicate entries for key
mountPath="/home/nonroot/.kubescape"
```

因此使用：

```text
/opt/kubescape-artifacts
```

---

## 11.5 Operator Framework Trigger

Helm values 中曾確認：

```yaml
operator:
  triggerSecurityFramework: true
```

在 Offline Artifacts 環境測試時，如果預設 Security Framework 造成錯誤，可以考慮調整：

```yaml
operator:
  triggerSecurityFramework: false
```

先由 Scheduler / API 明確指定 Framework。

---

# 12. 安裝 Kubescape Operator

先 render：

```bash
helm template kubescape \
  kubescape/kubescape-operator \
  --version 1.40.4 \
  -n kubescape \
  -f values-utf8.yaml \
  > /tmp/kubescape.yaml
```

確認 images：

```bash
grep 'image:' /tmp/kubescape.yaml | sort -u
```

正式安裝：

```bash
helm upgrade --install kubescape \
  kubescape/kubescape-operator \
  --version 1.40.4 \
  -n kubescape \
  --create-namespace \
  -f values-utf8.yaml
```

確認 release：

```bash
helm list -n kubescape
```

---

# 13. 確認 Kubescape Pods

```bash
kubectl get pod -n kubescape -o wide
```

主要元件：

```text
kubescape
operator
storage
kubevuln
grype-offline-db
```

確認 Deployment：

```bash
kubectl get deploy -n kubescape
```

確認 Service：

```bash
kubectl get svc -n kubescape
```

---

# 14. 確認 CRD

Kubescape Operator 會建立一系列 CRD。

確認：

```bash
kubectl api-resources | grep kubescape
```

以及：

```bash
kubectl api-resources | grep spdx
```

主要 Resource：

```text
ConfigurationScanSummary
WorkloadConfigurationScan
WorkloadConfigurationScanSummary
VulnerabilityManifest
VulnerabilityManifestSummary
VulnerabilitySummary
SBOMSyft
SBOMSyftFiltered
CollapseConfiguration
```

目前 API Group：

```text
spdx.softwarecomposition.kubescape.io/v1beta1
```

確認：

```bash
kubectl get configurationscansummaries
```

```bash
kubectl get workloadconfigurationscans -A
```

```bash
kubectl get workloadconfigurationscansummaries -A
```

---

# 15. Grype Offline DB

如果 Kubevuln 無法存取：

```text
https://grype.anchore.io/databases
```

可能看到：

```text
database does not exist
```

因此使用 Grype Offline DB。

確認：

```bash
kubectl get pod -n kubescape | grep grype
```

正常：

```text
grype-offline-db   1/1   Running
```

再確認：

```bash
kubectl get pod -n kubescape | grep kubevuln
```

Kubevuln 初始啟動時可能短暫：

```text
Readiness probe 503
```

待 Offline DB 初始化完成後會恢復：

```text
1/1 Running
```

---

# 16. Configuration Scan 驗證

查看：

```bash
kubectl get workloadconfigurationscansummaries -A
```

例如會有：

```text
serviceaccount-...
deployment-...
daemonset-...
configmap-...
rolebinding-...
clusterrole-...
```

計算數量：

```bash
kubectl get workloadconfigurationscansummaries -A \
  --no-headers | wc -l
```

查看單筆：

```bash
kubectl get workloadconfigurationscansummaries \
  -n <NAMESPACE> \
  <NAME> \
  -o yaml
```

---

# 17. Vulnerability Scan 驗證

```bash
kubectl get vulnerabilitymanifestsummaries -A
```

目前曾成功看到：

```text
NAMESPACE   NAME
alloy       daemonset-alloy-logs-apps-alloy
```

代表：

```text
Kubevuln
 ↓
Grype DB
 ↓
Storage
 ↓
Vulnerability CRD
```

這條路徑已經可以運作。

計算：

```bash
kubectl get vulnerabilitymanifestsummaries -A \
  --no-headers | wc -l
```

---

# 18. Kubescape CronJob

Kubescape Helm Chart 會建立 Scheduler CronJob。

確認：

```bash
kubectl get cronjob -n kubescape
```

目前實際看到：

```text
NAME                  SCHEDULE
kubescape-scheduler   4 14 * * *
kubevuln-scheduler    4 14 * * *
```

代表：

```text
kubescape-scheduler
→ Configuration Scan

kubevuln-scheduler
→ Vulnerability Scan
```

---

## 18.1 查看 Kubescape Scheduler

```bash
kubectl get cronjob kubescape-scheduler \
  -n kubescape \
  -o yaml
```

Scheduler 會 POST 到 Operator：

```text
http://operator:4002/v1/triggerAction
```

request body：

```json
{
  "commands": [
    {
      "CommandName": "kubescapeScan",
      "args": {
        "scanV1": {}
      }
    }
  ]
}
```

---

## 18.2 Scheduler 指定 Framework

Helm values 中：

```yaml
kubescapeScheduler:
  requestBody:
    commands:
      - CommandName: "kubescapeScan"
        args:
          scanV1: {}
```

如果希望排程明確只掃 NSA，可改成：

```yaml
kubescapeScheduler:
  requestBody:
    commands:
      - CommandName: "kubescapeScan"
        args:
          scanV1:
            targetType: framework
            targetNames:
              - nsa
```

這樣排程流程：

```text
CronJob
 ↓
Operator
 ↓
kubescapeScan
 ↓
Framework = NSA
 ↓
Kubescape Scanner
```

---

# 19. 手動從 CronJob 觸發 Scan

## Configuration Scan

```bash
kubectl create job \
  --from=cronjob/kubescape-scheduler \
  kubescape-manual-scan \
  -n kubescape
```

查看 Job：

```bash
kubectl get job -n kubescape
```

查看 Pod：

```bash
kubectl get pod -n kubescape \
  -l job-name=kubescape-manual-scan
```

查看 log：

```bash
kubectl logs \
  -n kubescape \
  job/kubescape-manual-scan
```

正常會看到：

```text
loading body from: /home/ks/request-body.json
method: POST
url: http://operator:4002/v1/triggerAction
response: ok
```

---

## Vulnerability Scan

手動建立：

```bash
kubectl create job \
  --from=cronjob/kubevuln-scheduler \
  kubevuln-manual-scan \
  -n kubescape
```

查看：

```bash
kubectl logs \
  -n kubescape \
  job/kubevuln-manual-scan
```

---

## 清除手動測試 Job

```bash
kubectl delete job \
  kubescape-manual-scan \
  -n kubescape
```

```bash
kubectl delete job \
  kubevuln-manual-scan \
  -n kubescape
```

---

# 20. Operator API 手動 Framework Scan

Operator Service：

```text
operator:4002
```

由 Bastion 存取時先 Port Forward。

Terminal 1：

```bash
kubectl port-forward \
  -n kubescape \
  svc/operator \
  4002:4002
```

成功：

```text
Forwarding from 127.0.0.1:4002 -> 4002
Forwarding from [::1]:4002 -> 4002
```

注意：

如果：

```text
error: lost connection to pod
```

代表 port-forward 斷線，不代表 Service 被停掉。

重新執行即可。

---

# 21. NSA / MITRE / CIS 測試

## NSA

```bash
curl -X POST \
  http://127.0.0.1:4002/v1/triggerAction \
  -H 'Content-Type: application/json' \
  -d '{
    "commands":[{
      "CommandName":"kubescapeScan",
      "args":{
        "scanV1":{
          "targetType":"framework",
          "targetNames":["nsa"]
        }
      }
    }]
  }'
```

回：

```text
ok
```

注意：

```text
ok
```

只代表 Operator 收到 Request。

真正成功必須看到：

```text
Framework scanned: NSA
Done scanning
Scan results saved
```

目前實際結果約：

```text
Overall compliance-score: 56
```

---

## MITRE

```bash
curl -X POST \
  http://127.0.0.1:4002/v1/triggerAction \
  -H 'Content-Type: application/json' \
  -d '{
    "commands":[{
      "CommandName":"kubescapeScan",
      "args":{
        "scanV1":{
          "targetType":"framework",
          "targetNames":["mitre"]
        }
      }
    }]
  }'
```

成功：

```text
Framework scanned: MITRE
```

目前實際：

```text
Overall compliance-score: 55
```

---

## CIS

```bash
curl -X POST \
  http://127.0.0.1:4002/v1/triggerAction \
  -H 'Content-Type: application/json' \
  -d '{
    "commands":[{
      "CommandName":"kubescapeScan",
      "args":{
        "scanV1":{
          "targetType":"framework",
          "targetNames":["cis-v1.10.0"]
        }
      }
    }]
  }'
```

---

## 建議一次只掃一個 Framework

不要短時間連續送：

```text
NSA
MITRE
CIS
```

建議：

```text
送 NSA
↓
等待 Done scanning
↓
再送 MITRE
↓
等待 Done scanning
↓
再送 CIS
```

Cluster 較大時會看到：

```text
Large cluster detected, enabling resource streaming
Streaming Kubernetes objects in batches...
```

因此一次 Scan 需要等待。

---

## 快速查看結果

```bash
kubectl logs -n kubescape deploy/kubescape --since=30m \
  | grep -E \
  'Framework scanned|Done scanning|Scan results saved|Overall compliance-score'
```

---

# 22. Scan Coverage Warning

NSA / MITRE 曾看到：

```text
Scan coverage score: 83% (24/26 controls evaluated)
```

未評估：

```text
C-0069
C-0070
```

缺少：

```text
hostdata.kubescape.cloud/v1beta0/KubeletInfo
```

相關 warning：

```text
node-agent status:
Failed to list OsReleaseFile CRDs
```

以及：

```text
failed to init host scanner
```

這代表目前沒有完整 Node Agent / Host Scanner 資料。

所以：

```text
83%
```

不是掃描失敗。

---

## Cloud Provider Warning

也可能看到：

```text
container.googleapis.com/v1/ClusterDescribe
eks.amazonaws.com/v1/ClusterDescribe
management.azure.com/v1/ClusterDescribe
```

以及：

```text
failed to get cloud provider
```

不是 GKE / EKS / AKS 時可以先忽略。

---

# 23. Storage 與 CRD Persist

Scanner 完成後，還必須把結果寫進 Kubescape Storage / CRD。

流程：

```text
Kubescape Scanner
 ↓
Scan Result
 ↓
Storage
 ↓
WorkloadConfigurationScanSummary
 ↓
Headlamp
```

因此：

```text
Scan 成功
```

不等於：

```text
Headlamp 一定收到所有結果
```

---

## 23.1 查看 Storage

```bash
kubectl get pod -n kubescape | grep storage
```

```bash
kubectl logs \
  -n kubescape \
  deploy/storage \
  --since=30m
```

正常啟動：

```text
starting storage server
APIServer starting
QueueManager - queue configuration
```

---

## 23.2 Persist Error

目前實際遇過：

```text
failed to store WorkloadConfigurationScanSummary manifest in storage
```

以及：

```text
failed to persist scan results to storage
```

錯誤 1：

```text
the server is currently unable to handle the request
```

錯誤 2：

```text
resource /argo/Deployment/argo-server//v1/argo/Service/argo-server
not found in report
```

可能造成：

```text
Scanner 完整 Scan
       ↓
Scan JSON 正常
       ↓
Storage Persist 部分失敗
       ↓
CRD 資料不完整
       ↓
Headlamp Score 與 CLI Score 不一致
```

---

## 23.3 快速檢查 Persist

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --since=30m \
  | grep -iE \
  'persist|storage|error|failed'
```

---

## 23.4 Storage API Discovery

如果看到：

```text
stale GroupVersion discovery:
spdx.softwarecomposition.kubescape.io/v1beta1
```

可能是 Storage API 暫時 unavailable。

確認：

```bash
kubectl api-resources | grep spdx
```

再確認 CRD/API：

```bash
kubectl get workloadconfigurationscansummaries -A
```

---

# 24. C-0240 Continuous Scan 問題

目前曾大量出現：

```text
framework: C-0240: framework from file not matching
```

特徵：

```text
Loading policies...
scanning failed
framework: C-0240: framework from file not matching
```

人工指定：

```text
NSA
MITRE
```

卻可以正常完成。

因此目前研判：

```text
Manual Framework Scan      OK
Continuous / Auto Scan     C-0240 Error
```

測試期間建議：

```yaml
capabilities:
  continuousScan: disable
```

先使用：

```text
CronJob
或
Operator API
```

進行完整 Framework Scan。

---

# 25. Headlamp Plugin

現有 Headlamp：

```bash
helm list -A | grep -i headlamp
```

目前：

```text
Release : headlamp
Namespace: headlamp
Chart   : headlamp-0.43.0
App     : 0.43.0
```

Deployment：

```bash
kubectl get deploy -A | grep -i headlamp
```

---

# 26. Headlamp Values

原 Headlamp 使用：

```yaml
automountServiceAccountToken: false

clusterRoleBinding:
  create: false

config:
  inCluster: false

  pluginsDir: /headlamp/plugins

  extraArgs:
    - -kubeconfig=/home/headlamp/.config/Headlamp/kubeconfigs/config
    - -oidc-ca-file=/etc/headlamp-oidc-ca/ca.crt
    - -oidc-use-access-token=true

  oidc:
    externalSecret:
      enabled: false
    secret:
      create: false

image:
  registry: utcsyquay.unimicron.com
  repository: registryk8s/headlamp
  tag: v0.43.0
  pullPolicy: IfNotPresent

podSecurityContext:
  fsGroup: 101
  fsGroupChangePolicy: OnRootMismatch

securityContext:
  allowPrivilegeEscalation: false
  privileged: false
  runAsGroup: 101
  runAsNonRoot: true
  runAsUser: 100

initContainers:
  - name: kubescape-plugin
    image: utcsyquay.unimicron.com/helm-charts/headlamp/kubescape-headlamp-plugin:v0.11.2
    imagePullPolicy: IfNotPresent
    command:
      - /bin/sh
      - -c
    args:
      - |
        echo "Copying Kubescape Headlamp plugin..."
        mkdir -p /headlamp/plugins
        cp -r /plugins/* /headlamp/plugins/
        echo "Installed plugins:"
        ls -lah /headlamp/plugins
    volumeMounts:
      - name: headlamp-plugins
        mountPath: /headlamp/plugins

volumeMounts:
  - name: headlamp-kubeconfig
    mountPath: /home/headlamp/.config/Headlamp/kubeconfigs/config
    readOnly: true
    subPath: config

  - name: headlamp-oidc-ca
    mountPath: /etc/headlamp-oidc-ca
    readOnly: true

  - name: headlamp-plugins
    mountPath: /headlamp/plugins

volumes:
  - name: headlamp-kubeconfig
    secret:
      defaultMode: 288
      secretName: headlamp-kubeconfig

  - name: headlamp-oidc-ca
    configMap:
      defaultMode: 292
      name: unimicron-ca

  - name: headlamp-plugins
    emptyDir: {}
```

---

# 27. 安裝 Kubescape Headlamp Plugin

Plugin：

```text
quay.io/kubescape/headlamp-plugin:v0.11.2
```

搬到 Registry：

```bash
skopeo copy \
  docker://quay.io/kubescape/headlamp-plugin:v0.11.2 \
  docker://utcsyquay.unimicron.com/helm-charts/headlamp/kubescape-headlamp-plugin:v0.11.2
```

Render：

```bash
helm template headlamp \
  headlamp/headlamp \
  --version 0.43.0 \
  -n headlamp \
  -f values.yaml \
  > /tmp/headlamp.yaml
```

檢查：

```bash
grep -n -A20 -B5 \
  'kubescape-plugin' \
  /tmp/headlamp.yaml
```

Upgrade：

```bash
helm upgrade headlamp \
  headlamp/headlamp \
  --version 0.43.0 \
  -n headlamp \
  -f values.yaml
```

---

# 28. Headlamp Plugin 驗證

Pod：

```bash
kubectl get pod -n headlamp
```

InitContainer log：

```bash
kubectl logs \
  -n headlamp \
  deploy/headlamp \
  -c kubescape-plugin
```

查看 Plugin：

```bash
kubectl exec \
  -n headlamp \
  deploy/headlamp -- \
  sh -c \
  'find /headlamp/plugins -maxdepth 3 -type f | head -50'
```

目前實際看到：

```text
/headlamp/plugins/kubescape-plugin/main.wasm
/headlamp/plugins/kubescape-plugin/vap-test-files-index.yaml
/headlamp/plugins/kubescape-plugin/vap-test-files.yaml
/headlamp/plugins/kubescape-plugin/frameworks.json
/headlamp/plugins/kubescape-plugin/validating-admission-policies.yaml
/headlamp/plugins/kubescape-plugin/controls.json
/headlamp/plugins/kubescape-plugin/basic-control-configuration.yaml
/headlamp/plugins/kubescape-plugin/main.js
/headlamp/plugins/kubescape-plugin/package.json
/headlamp/plugins/kubescape-plugin/rego-rules.json
```

---

## 28.1 Framework Definitions

查看：

```bash
kubectl exec \
  -n headlamp \
  deploy/headlamp -- \
  sh -c \
  'grep -n -iE "NSA|MITRE|CIS" \
  /headlamp/plugins/kubescape-plugin/frameworks.json \
  | head -50'
```

目前可看到：

```text
NSA
MITRE
cis-v1.10.0
cis-v1.12.0
cis-eks...
cis-aks...
```

---

# 29. Headlamp Compliance 計算方式

Headlamp 不直接使用 CLI 顯示的：

```text
Overall compliance-score
```

它是讀：

```text
WorkloadConfigurationScanSummary
```

再自己重新計算。

流程：

```text
frameworks.json
     +
WorkloadConfigurationScanSummary
     ↓
Framework controls
     ↓
controlID matching
     ↓
Control Compliance
     ↓
Framework Compliance
```

---

## 29.1 Control Score

概念：

```text
Passed Resources
---------------- × 100
Affected Resources
```

Headlamp Plugin 原始邏輯：

```text
某 Control 有找到 workload
→ Passed / Affected

某 Control 完全找不到 workload
→ 100%
```

因此如果 CRD 資料不完整：

```text
某些 Controls 不存在
↓
Headlamp 算成 100%
↓
Framework Score 可能偏高
```

---

# 30. CLI 與 Headlamp Score 差異

目前實際：

```text
Kubescape Scanner

NSA   約 56%
MITRE 約 55%
```

Headlamp 曾顯示：

```text
NSA    76%
MITRE  90%
CIS    99%
SOC2   98%
```

可能原因：

```text
Kubescape Scanner
      ↓
完整掃描
      ↓
Storage Persist
      ↓
部分結果失敗
      ↓
CRD 資料不完整
      ↓
Headlamp 重新計算
```

再加上：

```text
找不到 Control
→ Headlamp Control Score = 100%
```

因此：

```text
Headlamp Score
```

不一定等於：

```text
Kubescape Scanner Overall compliance-score
```

在 Storage 問題修正以前，Framework 真實掃描結果建議以：

```text
Kubescape Scanner Log
```

為主要依據。

---

# 31. Namespace Compliance 注意事項

Headlamp：

```text
Compliance
→ Namespaces
```

例如：

```text
alloy          100%
apisix          65%
argo            79%
argocd          81%
beyla           52%
calico-system   65%
```

這些不是：

```text
NSA Namespace Score
MITRE Namespace Score
CIS Namespace Score
```

Plugin 的 Namespace 邏輯是：

```text
Namespace
 ↓
所有 WorkloadConfigurationScanSummary
 ↓
所有 spec.controls
 ↓
passed / total
```

沒有 Framework filter。

因此：

```text
按 NSA
→ Namespace % 不變

按 MITRE
→ Namespace % 不變

按 CIS
→ Namespace % 不變
```

屬於目前 Plugin 程式邏輯。

---

## 31.1 Framework 上方百分比

上方：

```text
99% CIS
90% MITRE
76% NSA
98% SOC2
```

這一區有依：

```text
framework.controls
```

重新計算。

---

## 31.2 Clusters 頁籤

`Clusters` 頁籤的 Plugin 程式有使用：

```text
frameworkComplianceScore(...)
```

因此比 `Namespaces` 更適合拿來比較 Framework。

---

# 32. 常用查核指令

## 所有 Pods

```bash
kubectl get pod -n kubescape -o wide
```

---

## CronJobs

```bash
kubectl get cronjob -n kubescape
```

---

## Jobs

```bash
kubectl get job -n kubescape
```

---

## Configuration Scan

```bash
kubectl get workloadconfigurationscans -A
```

```bash
kubectl get workloadconfigurationscansummaries -A
```

---

## Configuration Scan 數量

```bash
kubectl get workloadconfigurationscansummaries -A \
  --no-headers | wc -l
```

---

## Vulnerability

```bash
kubectl get vulnerabilitymanifestsummaries -A
```

---

## Cluster Summary

```bash
kubectl get configurationscansummaries
```

---

## Kubescape Scanner Log

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --tail=200
```

---

## 即時 Log

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  -f
```

---

## Storage

```bash
kubectl logs \
  -n kubescape \
  deploy/storage \
  --tail=200
```

---

## Kubevuln

```bash
kubectl logs \
  -n kubescape \
  deploy/kubevuln \
  --tail=200
```

---

## Framework 結果

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --since=30m \
  | grep -E \
  'Framework scanned|Done scanning|Scan results saved|Overall compliance-score'
```

---

## Persist Error

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --since=30m \
  | grep -iE \
  'persist|storage|error|failed'
```

---

## Offline Artifact Env

```bash
kubectl get deploy kubescape \
  -n kubescape \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' \
  | grep -iE \
  'ARTIFACT|CACHE|DOWNLOAD|POLICY'
```

---

## Artifact Mount

```bash
kubectl get deploy kubescape \
  -n kubescape \
  -o yaml \
  | grep -A12 -B12 \
  '/opt/kubescape-artifacts'
```

---

## Headlamp Plugin

```bash
kubectl exec \
  -n headlamp \
  deploy/headlamp -- \
  sh -c \
  'find /headlamp/plugins -maxdepth 3 -type f | head -50'
```

---

# 33. Troubleshooting

## 33.1 `framework from file not matching`

```text
framework: C-0240: framework from file not matching
```

人工 NSA / MITRE Scan 正常時，優先確認：

```yaml
capabilities:
  continuousScan: disable
```

---

## 33.2 Storage unavailable

```text
the server is currently unable to handle the request
```

確認：

```bash
kubectl get pod -n kubescape | grep storage
```

```bash
kubectl describe pod \
  -n kubescape \
  <STORAGE_POD>
```

如果曾 Restart：

```bash
kubectl logs \
  -n kubescape \
  <STORAGE_POD> \
  --previous
```

---

## 33.3 `argo-server not found in report`

```text
resource /argo/Deployment/argo-server//v1/argo/Service/argo-server
not found in report
```

目前仍需追查 Kubescape Report / Storage 關聯處理。

確認：

```bash
kubectl get deploy,svc \
  -n argo \
  | grep argo-server
```

---

## 33.4 Events Forbidden

```text
events is forbidden
```

例如：

```text
User "system:serviceaccount:kubescape:kubescape"
cannot create resource "events"
```

表示 Kubescape SA 無權建立 K8s Event。

目前不影響 Framework Scanner 完成。

---

## 33.5 SecurityExceptions Forbidden

```text
securityexceptions.kubescape.io is forbidden
```

目前沒有權限 list：

```text
securityexceptions
```

Kubescape 仍會：

```text
continuing with primary exceptions only
```

---

## 33.6 Host Scanner

```text
Failed to list OsReleaseFile CRDs
```

以及：

```text
failed to init host scanner
```

目前造成：

```text
C-0069
C-0070
```

未被評估。

---

## 33.7 Port Forward 自動中斷

看到：

```text
error: lost connection to pod
```

不是 Service 停止。

重新：

```bash
kubectl port-forward \
  -n kubescape \
  svc/operator \
  4002:4002
```

也可直接綁 Pod：

```bash
kubectl port-forward \
  -n kubescape \
  pod/<OPERATOR_POD> \
  4002:4002
```

---

# 34. 最終狀態

目前確認：

```text
Kubescape Operator           OK
Private Registry             OK
Offline Artifacts            OK
Artifacts PVC                OK
KS_CACHE_DIR                 OK
KS_DOWNLOAD_ARTIFACTS=false  OK

Configuration Scan           OK
NSA Framework Scan           OK
MITRE Framework Scan         OK

Kubevuln                     OK
Grype Offline DB             OK
Vulnerability Scan           OK

kubescape-scheduler          OK
kubevuln-scheduler           OK
Manual CronJob Scan          OK
Operator API Scan            OK

Storage                      可運作
Storage Persist              部分有 Error

Headlamp                     OK
Kubescape Headlamp Plugin    OK
Compliance UI                OK
Vulnerability UI             OK
```

目前待處理：

```text
1. Continuous Scan C-0240 Error

2. Storage Persist Error

3. argo-server resource not found in report

4. Headlamp Framework Score
   與 Scanner Overall Compliance Score 差異

5. Host Scanner / KubeletInfo
   C-0069 / C-0070

6. SecurityExceptions RBAC

7. Event RBAC
```

---

# 35. 建議目前正式使用方式

在目前問題完全排除以前，建議：

```text
continuousScan = disable
```

改由：

```text
kubescape-scheduler
```

進行完整 Configuration Scan。

Framework 建議明確指定：

```text
NSA
```

或依需求指定：

```text
MITRE
CIS
```

判斷 Framework 實際 Compliance Score 時，優先看：

```bash
kubectl logs -n kubescape deploy/kubescape --since=30m \
  | grep -E \
  'Framework scanned|Overall compliance-score'
```

Headlamp 則主要用於：

```text
Configuration Issues 瀏覽
Failed Controls
Resources
Namespaces
Vulnerabilities
快速視覺化
```

Storage Persist 問題完全解決前，不建議直接把 Headlamp 上方 Framework 百分比當成最終稽核數字。
