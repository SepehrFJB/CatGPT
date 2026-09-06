#!/usr/bin/env bash

# ==============================================================================
# CatGPT Telegram Bot - Server Installation & Management Script
# Supports: Ubuntu, Debian, CentOS, AlmaLinux, Rocky Linux, Fedora, Arch
# ==============================================================================

set -e

# ANSI Color Codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Determine script and project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Print banner
print_banner() {
    echo -e "${CYAN}${BOLD}"
    echo "======================================================"
    echo "         🐾 CatGPT Telegram Bot Server Setup         "
    echo "======================================================"
    echo -e "${NC}"
}

# Logging Helpers
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root or with sudo
check_privileges() {
    if [ "$EUID" -ne 0 ]; then
        if ! command -v sudo &> /dev/null; then
            log_error "This script requires superuser privileges. Please install sudo or run as root."
            exit 1
        fi
        SUDO_CMD="sudo"
    else
        SUDO_CMD=""
    fi
}

# Detect Docker Compose Command (v2 plugin or v1 standalone)
get_docker_compose_cmd() {
    if docker compose version &> /dev/null; then
        echo "docker compose"
    elif command -v docker-compose &> /dev/null; then
        echo "docker-compose"
    else
        echo ""
    fi
}

# Install Docker and Docker Compose if not installed
install_docker_if_needed() {
    log_info "Checking Docker installation..."

    if command -v docker &> /dev/null; then
        log_success "Docker is already installed ($(docker --version))."
    else
        log_warn "Docker is not installed. Installing Docker automatically..."
        check_privileges
        
        # Install curl if missing
        if ! command -v curl &> /dev/null; then
            log_info "Installing curl..."
            if command -v apt-get &> /dev/null; then
                $SUDO_CMD apt-get update -y && $SUDO_CMD apt-get install -y curl
            elif command -v dnf &> /dev/null; then
                $SUDO_CMD dnf install -y curl
            elif command -v yum &> /dev/null; then
                $SUDO_CMD yum install -y curl
            elif command -v pacman &> /dev/null; then
                $SUDO_CMD pacman -Sy --noconfirm curl
            fi
        fi

        # Install Docker using official script
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
        $SUDO_CMD sh /tmp/get-docker.sh
        rm -f /tmp/get-docker.sh

        # Start and enable Docker service
        if command -v systemctl &> /dev/null; then
            $SUDO_CMD systemctl enable docker || true
            $SUDO_CMD systemctl start docker || true
        fi

        log_success "Docker successfully installed."
    fi

    # Check for docker compose
    COMPOSE_CMD=$(get_docker_compose_cmd)
    if [ -z "$COMPOSE_CMD" ]; then
        log_warn "Docker Compose is not installed. Installing Docker Compose plugin..."
        check_privileges
        if command -v apt-get &> /dev/null; then
            $SUDO_CMD apt-get update -y && $SUDO_CMD apt-get install -y docker-compose-plugin
        elif command -v dnf &> /dev/null; then
            $SUDO_CMD dnf install -y docker-compose-plugin
        elif command -v yum &> /dev/null; then
            $SUDO_CMD yum install -y docker-compose-plugin
        else
            log_info "Downloading standalone docker-compose binary..."
            $SUDO_CMD curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
            $SUDO_CMD chmod +x /usr/local/bin/docker-compose
        fi
        COMPOSE_CMD=$(get_docker_compose_cmd)
    fi

    if [ -z "$COMPOSE_CMD" ]; then
        log_error "Failed to setup Docker Compose. Please install Docker Compose manually."
        exit 1
    fi

    log_success "Docker Compose is ready ($($COMPOSE_CMD version))."
}

# Ensure persistent directories and files exist
prepare_filesystem() {
    log_info "Preparing filesystem paths and permissions..."
    mkdir -p logs backups downloads
    
    # Touch sqlite db if not present so docker won't create a folder instead of file
    if [ ! -f "catgpt.sqlite3" ]; then
        touch catgpt.sqlite3
    fi

    chmod 755 logs backups downloads || true
    log_success "Filesystem structure verified."
}

# Interactive configuration for .env file
setup_env() {
    if [ -f ".env" ]; then
        log_info "Existing .env file found."
        read -rp "Do you want to reconfigure .env settings? [y/N]: " RECONF
        if [[ ! "$RECONF" =~ ^[Yy]$ ]]; then
            return 0
        fi
    fi

    echo -e "\n${CYAN}${BOLD}--- CatGPT Environment Configuration ---${NC}"
    echo "Press Enter to use default values where shown in brackets."

    # BOT_TOKEN
    while true; do
        read -rp "1) Telegram Bot Token (from @BotFather) [Required]: " INPUT_BOT_TOKEN
        INPUT_BOT_TOKEN=$(echo "$INPUT_BOT_TOKEN" | xargs)
        if [ -n "$INPUT_BOT_TOKEN" ]; then
            break
        fi
        log_warn "Bot Token cannot be empty!"
    done

    # DEEPSEEK_API_KEY
    while true; do
        read -rp "2) DeepSeek / OpenAI API Key [Required]: " INPUT_API_KEY
        INPUT_API_KEY=$(echo "$INPUT_API_KEY" | xargs)
        if [ -n "$INPUT_API_KEY" ]; then
            break
        fi
        log_warn "API Key cannot be empty!"
    done

    # DEEPSEEK_BASE_URL
    read -rp "3) API Base URL [https://api.deepseek.com]: " INPUT_BASE_URL
    INPUT_BASE_URL=${INPUT_BASE_URL:-https://api.deepseek.com}

    # DEEPSEEK_MODEL
    read -rp "4) DeepSeek Model [deepseek-v4-flash-vision-exp]: " INPUT_MODEL
    INPUT_MODEL=${INPUT_MODEL:-deepseek-v4-flash-vision-exp}

    # ADMIN_IDS
    read -rp "5) Admin Telegram User IDs (comma-separated, e.g. 12345678,87654321): " INPUT_ADMIN_IDS
    INPUT_ADMIN_IDS=$(echo "$INPUT_ADMIN_IDS" | xargs)

    # SUPPORT_URL
    read -rp "6) Support Username or URL (e.g. admin_user or https://t.me/admin): " INPUT_SUPPORT_URL
    INPUT_SUPPORT_URL=$(echo "$INPUT_SUPPORT_URL" | xargs)

    # FREE_DAILY_LIMIT
    read -rp "7) Free Daily Limit [10]: " INPUT_FREE_LIMIT
    INPUT_FREE_LIMIT=${INPUT_FREE_LIMIT:-10}

    # PLUS_DAILY_LIMIT
    read -rp "8) Plus Daily Limit [50]: " INPUT_PLUS_LIMIT
    INPUT_PLUS_LIMIT=${INPUT_PLUS_LIMIT:-50}

    # PLUS_STARS_PRICE
    read -rp "9) Plus Stars Price [100]: " INPUT_STARS_PRICE
    INPUT_STARS_PRICE=${INPUT_STARS_PRICE:-100}

    # Write .env file
    cat <<EOF > .env
BOT_TOKEN=${INPUT_BOT_TOKEN}
DEEPSEEK_API_KEY=${INPUT_API_KEY}
DEEPSEEK_BASE_URL=${INPUT_BASE_URL}
DEEPSEEK_MODEL=${INPUT_MODEL}
DEEPSEEK_VISION_DETAIL=low
DEEPSEEK_THINKING_MODE=disabled
DEEPSEEK_THINKING_EFFORT=low
ADMIN_IDS=${INPUT_ADMIN_IDS}
SUPPORT_URL=${INPUT_SUPPORT_URL}
FREE_DAILY_LIMIT=${INPUT_FREE_LIMIT}
PLUS_DAILY_LIMIT=${INPUT_PLUS_LIMIT}
PLUS_STARS_PRICE=${INPUT_STARS_PRICE}
PLUS_PERIOD_SECONDS=2592000
MAX_INPUT_CHARS=1000
MAX_OUTPUT_TOKENS=800
DATABASE_URL=sqlite+aiosqlite:///catgpt.sqlite3
GROUP_WELCOME_PHOTO=assets/catgptbot.png
EOF

    chmod 600 .env
    log_success ".env configuration successfully saved."
}

# Build and start container
start_container() {
    COMPOSE_CMD=$(get_docker_compose_cmd)
    if [ -z "$COMPOSE_CMD" ]; then
        log_error "Docker Compose command not found."
        exit 1
    fi

    log_info "Building and starting CatGPT Docker container..."
    $COMPOSE_CMD up -d --build

    log_info "Verifying bot status..."
    sleep 3

    if $COMPOSE_CMD ps | grep -q "catgpt-bot"; then
        log_success "CatGPT Bot is up and running!"
    else
        log_warn "Container status check returned an unexpected result. Showing recent logs:"
        $COMPOSE_CMD logs --tail=50
    fi
}

# Stop container
stop_container() {
    COMPOSE_CMD=$(get_docker_compose_cmd)
    log_info "Stopping CatGPT Bot container..."
    $COMPOSE_CMD down
    log_success "CatGPT Bot stopped."
}

# Restart container
restart_container() {
    COMPOSE_CMD=$(get_docker_compose_cmd)
    log_info "Restarting CatGPT Bot container..."
    $COMPOSE_CMD restart
    log_success "CatGPT Bot restarted."
}

# Stream logs
show_logs() {
    COMPOSE_CMD=$(get_docker_compose_cmd)
    log_info "Streaming logs for CatGPT Bot (Press Ctrl+C to exit)..."
    $COMPOSE_CMD logs -f --tail=100
}

# Container status
show_status() {
    COMPOSE_CMD=$(get_docker_compose_cmd)
    echo -e "\n${CYAN}${BOLD}=== Container Status ===${NC}"
    $COMPOSE_CMD ps
    echo -e "\n${CYAN}${BOLD}=== Resource Stats ===${NC}"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}" catgpt-bot 2>/dev/null || true
}

# Update bot from git repository
update_bot() {
    log_info "Updating CatGPT from git repository..."
    if [ -d ".git" ]; then
        git pull
    else
        log_warn "Not a git repository. Skipping git pull."
    fi

    COMPOSE_CMD=$(get_docker_compose_cmd)
    log_info "Rebuilding and restarting container..."
    $COMPOSE_CMD up -d --build
    log_success "CatGPT Bot successfully updated!"
}

# Create instant database backup
create_backup() {
    log_info "Creating instant database backup..."
    mkdir -p backups
    STAMP=$(date +'%Y%m%d-%H%M%S')
    if [ -f "catgpt.sqlite3" ]; then
        cp catgpt.sqlite3 "backups/catgpt-manual-${STAMP}.sqlite3"
        log_success "Backup saved to backups/catgpt-manual-${STAMP}.sqlite3"
    else
        log_warn "catgpt.sqlite3 not found."
    fi
    echo -e "\n${CYAN}Existing Backups:${NC}"
    ls -lh backups/*.sqlite3 2>/dev/null || echo "No backups found."
}

# Display help menu
show_help() {
    echo -e "${BOLD}Usage:${NC} ./install_server.sh [command]"
    echo ""
    echo -e "${BOLD}Commands:${NC}"
    echo "  (empty) / install  Full automated installation, configuration, and launch"
    echo "  start              Start the bot container"
    echo "  stop               Stop the bot container"
    echo "  restart            Restart the bot container"
    echo "  logs               Stream live bot logs in real-time"
    echo "  status             Check bot container status and resource usage"
    echo "  update             Pull latest code from git and rebuild container"
    echo "  backup             Create a manual local database backup"
    echo "  help | -h          Show this help message"
    echo ""
    echo -e "${BOLD}Examples:${NC}"
    echo "  ./install_server.sh          # Install and run"
    echo "  ./install_server.sh logs     # View live logs"
    echo "  ./install_server.sh restart  # Restart bot"
}

# Main Execution Flow
main() {
    COMMAND="${1:-install}"

    case "$COMMAND" in
        install|"")
            print_banner
            install_docker_if_needed
            prepare_filesystem
            setup_env
            start_container
            echo ""
            echo -e "${GREEN}${BOLD}======================================================${NC}"
            echo -e "${GREEN}${BOLD}       🚀 CatGPT Bot Successfully Deployed!          ${NC}"
            echo -e "${GREEN}${BOLD}======================================================${NC}"
            echo -e "Helpful commands:"
            echo -e "  • Live logs:    ${CYAN}./install_server.sh logs${NC}"
            echo -e "  • Restart bot:  ${CYAN}./install_server.sh restart${NC}"
            echo -e "  • Bot status:   ${CYAN}./install_server.sh status${NC}"
            echo -e "  • Stop bot:     ${CYAN}./install_server.sh stop${NC}"
            echo -e "  • Update code:  ${CYAN}./install_server.sh update${NC}"
            echo -e "  • Manual backup:${CYAN}./install_server.sh backup${NC}"
            echo ""
            ;;
        start)
            start_container
            ;;
        stop)
            stop_container
            ;;
        restart)
            restart_container
            ;;
        logs)
            show_logs
            ;;
        status)
            show_status
            ;;
        update)
            update_bot
            ;;
        backup)
            create_backup
            ;;
        help|-h|--help)
            show_help
            ;;
        *)
            log_error "Unknown command: $COMMAND"
            show_help
            exit 1
            ;;
    esac
}

main "$@"

