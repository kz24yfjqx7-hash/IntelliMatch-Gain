# 能源可信数据空间平台 · 便捷命令
SHELL := /bin/bash
COMPOSE := docker compose
SCRIPTS := $(wildcard deploy/*.sh packaging/*.sh)

.PHONY: help env up down logs ps dev-up dev-down build-images check package package-amd64 package-arm64 offline clean

help:            ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*##' '{printf "  %-16s %s\n", $$1, $$2}'

env:             ## 从 .env.example 生成 .env（已存在则跳过）
	@test -f .env || cp .env.example .env; echo ".env 就绪"

up: env          ## 启动全部五个服务（需 backend/）
	$(COMPOSE) up -d --build

down:            ## 停止（保留数据卷）
	$(COMPOSE) down

logs:            ## 跟踪日志
	$(COMPOSE) logs -f --tail=100

ps:              ## 容器状态
	$(COMPOSE) ps

dev-up: env      ## 乙方独立联调：仅 algo-service（加 PROFILE=mock 再起 MSW 前端）
	$(COMPOSE) -f docker-compose.dev.yml $(if $(PROFILE),--profile $(PROFILE),) up -d --build

dev-down:        ## 停止独立联调
	$(COMPOSE) -f docker-compose.dev.yml --profile mock down

build-images: env ## 本机架构构建 algo-service 与 frontend 镜像
	$(COMPOSE) build algo-service frontend

check:           ## 语法检查：bash -n、shellcheck（若有）、compose config
	@for f in $(SCRIPTS); do bash -n $$f && echo "bash -n OK  $$f"; done
	@if command -v shellcheck >/dev/null; then shellcheck -S warning $(SCRIPTS); else echo "shellcheck 未安装，跳过"; fi
	@test -f .env || cp .env.example .env
	@$(COMPOSE) config -q && echo "docker-compose.yml OK"
	@$(COMPOSE) -f docker-compose.dev.yml config -q && echo "docker-compose.dev.yml OK"

package:         ## 双架构离线安装包（需 sudo、联网、buildx+QEMU）
	sudo ./packaging/build.sh --arch all

package-amd64:   ## 仅 x86_64 安装包
	sudo ./packaging/build.sh --arch amd64

package-arm64:   ## 仅树莓派安装包
	sudo ./packaging/build.sh --arch arm64

offline:         ## 单架构快速离线镜像包（开发联调）
	./deploy/build-offline.sh $(if $(wildcard backend),,--skip-backend)

clean:           ## 清理构建产物
	rm -rf build dist
