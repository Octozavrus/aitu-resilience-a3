# Synthetic AITU Assignment 3 - one command per step. Run `make help`.
NS        := aitu-a3
IMAGE     := aitu-a3/registration:1.0
# Set to kind / minikube / docker-desktop. docker-desktop needs no image loading.
CLUSTER   ?= docker-desktop
PY        ?= python3

.PHONY: help venv model image deploy status smoke experiments dr-drill figures report all clean

help:
	@echo "make venv          - install Python deps for the analysis scripts"
	@echo "make model         - Part B: availability model -> results/availability.json"
	@echo "make image         - build the Registration image (and load it into kind/minikube)"
	@echo "make deploy        - apply k8s/ and wait until everything is Ready"
	@echo "make smoke         - one request through gateway -> registration -> DB"
	@echo "make experiments   - Part D: run the 3 (+1 bonus) failure experiments (~6 min)"
	@echo "make dr-drill      - Part E: backup, drop tables, restore, time it"
	@echo "make figures       - regenerate diagrams/"
	@echo "make report        - build report/Assignment3_Report.docx"
	@echo "make all           - model image deploy experiments dr-drill figures report"
	@echo "make clean         - delete the namespace"

venv:
	$(PY) -m pip install -r requirements-dev.txt

model:
	$(PY) scripts/availability.py

image:
	docker build -t $(IMAGE) app/registration
ifeq ($(CLUSTER),kind)
	kind load docker-image $(IMAGE)
endif
ifeq ($(CLUSTER),minikube)
	minikube image load $(IMAGE)
endif

deploy:
	kubectl apply -f k8s/00-namespace.yaml
	kubectl -n $(NS) create configmap loadgen-script --from-file=app/loadgen/loadgen.py \
	  --dry-run=client -o yaml | kubectl apply -f -
	kubectl apply -f k8s/10-postgres.yaml -f k8s/20-registration.yaml
	kubectl -n $(NS) rollout status statefulset/registration-db --timeout=180s
	kubectl -n $(NS) rollout status deployment/registration-service --timeout=180s
	kubectl apply -f k8s/30-gateway.yaml -f k8s/40-loadgen.yaml
	kubectl -n $(NS) rollout status deployment/api-gateway --timeout=120s
	kubectl -n $(NS) wait --for=condition=Ready pod/student-traffic --timeout=180s
	kubectl -n $(NS) get pods -o wide

status:
	kubectl -n $(NS) get pods,svc,pdb,endpointslices -o wide

smoke:
	kubectl -n $(NS) exec student-traffic -- python -c "import urllib.request as u; \
	print(u.urlopen('http://api-gateway/api/registration/courses', timeout=3).read().decode())"

experiments:
	$(PY) scripts/experiments.py

dr-drill:
	$(PY) scripts/dr_drill.py

figures:
	$(PY) scripts/diagrams.py

report: figures
	$(PY) scripts/build_report.py

all: model image deploy experiments dr-drill report

clean:
	kubectl delete namespace $(NS) --ignore-not-found
