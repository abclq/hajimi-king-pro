import os
import random
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Union, Any

# 添加项目根目录到模块搜索路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

from common.Logger import logger
from common.config import Config
from common.translations import get_translator
from common import state
from utils.github_client import GitHubClient
from utils.file_manager import file_manager, Checkpoint, checkpoint
from utils.sync_utils import sync_utils
from utils.migration import KeyMigration
from utils.key_validator import key_validator

# 获取翻译函数
t = get_translator().t

# 创建GitHub工具实例和文件管理器（传递认证模式和session cookie）
github_utils = GitHubClient.create_instance(Config.GITHUB_TOKENS, Config.GITHUB_AUTH_MODE, Config.GITHUB_SESSIONS)

# 统计信息
skip_stats = {
    "time_filter": 0,
    "sha_duplicate": 0,
    "age_filter": 0,
    "doc_filter": 0
}

def normalize_query(query: str) -> str:
    query = " ".join(query.split())

    parts = []
    i = 0
    while i < len(query):
        if query[i] == '"':
            end_quote = query.find('"', i + 1)
            if end_quote != -1:
                parts.append(query[i:end_quote + 1])
                i = end_quote + 1
            else:
                parts.append(query[i])
                i += 1
        elif query[i] == ' ':
            i += 1
        else:
            start = i
            while i < len(query) and query[i] != ' ':
                i += 1
            parts.append(query[start:i])

    quoted_strings = []
    language_parts = []
    filename_parts = []
    path_parts = []
    other_parts = []

    for part in parts:
        if part.startswith('"') and part.endswith('"'):
            quoted_strings.append(part)
        elif part.startswith('language:'):
            language_parts.append(part)
        elif part.startswith('filename:'):
            filename_parts.append(part)
        elif part.startswith('path:'):
            path_parts.append(part)
        elif part.strip():
            other_parts.append(part)

    normalized_parts = []
    normalized_parts.extend(sorted(quoted_strings))
    normalized_parts.extend(sorted(other_parts))
    normalized_parts.extend(sorted(language_parts))
    normalized_parts.extend(sorted(filename_parts))
    normalized_parts.extend(sorted(path_parts))

    return " ".join(normalized_parts)


def extract_keys_from_content(content: str) -> List[str]:
    pattern = r'(AIzaSy[A-Za-z0-9\-_]{33})'
    return re.findall(pattern, content)


def should_skip_item(item: Dict[str, Any], checkpoint: Checkpoint) -> tuple[bool, str]:
    """
    检查是否应该跳过处理此item
    
    Returns:
        tuple: (should_skip, reason)
    """
    # 检查增量扫描时间
    if checkpoint.last_scan_time:
        try:
            last_scan_dt = datetime.fromisoformat(checkpoint.last_scan_time)
            repo_pushed_at = item["repository"].get("pushed_at")
            if repo_pushed_at:
                repo_pushed_dt = datetime.strptime(repo_pushed_at, "%Y-%m-%dT%H:%M:%SZ")
                if repo_pushed_dt <= last_scan_dt:
                    skip_stats["time_filter"] += 1
                    return True, "time_filter"
        except Exception as e:
            pass

    # 检查SHA是否已扫描
    if item.get("sha") in checkpoint.scanned_shas:
        skip_stats["sha_duplicate"] += 1
        return True, "sha_duplicate"

    # 检查仓库年龄
    repo_pushed_at = item["repository"].get("pushed_at")
    if repo_pushed_at:
        repo_pushed_dt = datetime.strptime(repo_pushed_at, "%Y-%m-%dT%H:%M:%SZ")
        if repo_pushed_dt < datetime.utcnow() - timedelta(days=Config.DATE_RANGE_DAYS):
            skip_stats["age_filter"] += 1
            return True, "age_filter"

    # 检查文档和示例文件
    lowercase_path = item["path"].lower()
    if any(token in lowercase_path for token in Config.FILE_PATH_BLACKLIST):
        skip_stats["doc_filter"] += 1
        return True, "doc_filter"

    return False, ""


def process_item(item: Dict[str, Any]) -> int:
    """
    处理单个GitHub搜索结果item（异步验证模式）
    
    Returns:
        int: 找到的密钥数量
    """
    delay = random.uniform(1, 4)
    file_url = item["html_url"]

    # 简化日志输出，只显示关键信息
    repo_name = item["repository"]["full_name"]
    file_path = item["path"]
    time.sleep(delay)

    content = github_utils.get_file_content(item)
    if not content:
        logger.warning(t('failed_fetch_content', file_url))
        return 0

    keys = extract_keys_from_content(content)

    # 过滤占位符密钥
    filtered_keys = []
    for key in keys:
        context_index = content.find(key)
        if context_index != -1:
            snippet = content[context_index:context_index + 45]
            if "..." in snippet or "YOUR_" in snippet.upper():
                continue
        filtered_keys.append(key)
    
    # 去重处理
    keys = list(set(filtered_keys))

    if not keys:
        return 0

    logger.info(t('found_keys', len(keys)))

    # 将所有密钥添加到异步验证队列
    for key in keys:
        key_validator.add_key(key, repo_name, file_path, file_url)
    
    logger.info(f"📥 已添加 {len(keys)} 个密钥到验证队列")

    return len(keys)


def validate_gemini_key(api_key: str) -> Union[bool, str]:
    try:
        time.sleep(random.uniform(0.5, 1.5))

        # 获取随机代理配置
        proxy_config = Config.get_random_proxy()
        
        client_options = {
            "api_endpoint": "generativelanguage.googleapis.com"
        }
        
        # 如果有代理配置，添加到client_options中
        if proxy_config:
            os.environ['grpc_proxy'] = proxy_config.get('http')

        genai.configure(
            api_key=api_key,
            client_options=client_options,
        )

        model = genai.GenerativeModel(Config.HAJIMI_CHECK_MODEL)
        response = model.generate_content("hi")
        return "ok"
    except (google_exceptions.PermissionDenied, google_exceptions.Unauthenticated) as e:
        return "not_authorized_key"
    except google_exceptions.TooManyRequests as e:
        return "rate_limited"
    except Exception as e:
        if "429" in str(e) or "rate limit" in str(e).lower() or "quota" in str(e).lower():
            return "rate_limited:429"
        elif "403" in str(e) or "SERVICE_DISABLED" in str(e) or "API has not been used" in str(e):
            return "disabled"
        else:
            return f"error:{e.__class__.__name__}"


def validate_paid_model_key(api_key: str) -> Union[bool, str]:
    """
    验证密钥是否支持付费模型
    
    Args:
        api_key: Gemini API密钥
        
    Returns:
        "ok" 表示付费模型可用，其他字符串表示验证失败的原因
    """
    try:
        time.sleep(random.uniform(0.5, 1.5))

        # 获取随机代理配置
        proxy_config = Config.get_random_proxy()
        
        client_options = {
            "api_endpoint": "generativelanguage.googleapis.com"
        }
        
        # 如果有代理配置，添加到client_options中
        if proxy_config:
            os.environ['grpc_proxy'] = proxy_config.get('http')

        genai.configure(
            api_key=api_key,
            client_options=client_options,
        )

        model = genai.GenerativeModel(Config.HAJIMI_PAID_MODEL)
        response = model.generate_content("hi")
        return "ok"
    except (google_exceptions.PermissionDenied, google_exceptions.Unauthenticated) as e:
        return "not_authorized_for_paid"
    except google_exceptions.TooManyRequests as e:
        return "rate_limited"
    except Exception as e:
        if "429" in str(e) or "rate limit" in str(e).lower() or "quota" in str(e).lower():
            return "rate_limited"
        elif "403" in str(e) or "SERVICE_DISABLED" in str(e) or "API has not been used" in str(e):
            return "disabled"
        elif "not found" in str(e).lower() or "404" in str(e):
            return "model_not_found"
        else:
            return f"error:{e.__class__.__name__}"


def print_skip_stats():
    """打印跳过统计信息"""
    total_skipped = sum(skip_stats.values())
    if total_skipped > 0:
        logger.info(t('skip_stats', total_skipped, skip_stats['time_filter'], skip_stats['sha_duplicate'], skip_stats['age_filter'], skip_stats['doc_filter']))


def reset_skip_stats():
    """重置跳过统计"""
    global skip_stats
    skip_stats = {"time_filter": 0, "sha_duplicate": 0, "age_filter": 0, "doc_filter": 0}


def main():
    start_time = datetime.now()

    # 打印系统启动信息
    logger.info("=" * 60)
    logger.info(t('system_starting'))
    logger.info("=" * 60)
    logger.info(t('started_at', start_time.strftime('%Y-%m-%d %H:%M:%S')))

    # 1. 检查配置
    if not Config.check():
        logger.info(t('config_check_failed'))
        sys.exit(1)
    
    # 1.5. 检查是否需要数据迁移（从文本文件迁移到数据库）
    if Config.STORAGE_TYPE == 'sql' and file_manager.db_manager:
        migration = KeyMigration(Config.DATA_PATH, file_manager.db_manager)
        if migration.check_need_migration():
            logger.info(t('migration_check_detected'))
            if migration.migrate():
                logger.info(t('migration_check_completed'))
            else:
                logger.error(t('migration_check_failed'))
                logger.info(t('migration_check_hint'))
                sys.exit(1)
        else:
            logger.info(t('migration_check_not_needed'))
    
    # 2. 检查文件管理器
    if not file_manager.check():
        logger.error(t('filemanager_check_failed'))
        sys.exit(1)

    # 2.5. 显示SyncUtils状态和队列信息
    if sync_utils.balancer_enabled:
        logger.info(t('syncutils_ready'))
        
    # 显示队列状态
    balancer_queue_count = len(checkpoint.wait_send_balancer)
    gpt_load_queue_count = len(checkpoint.wait_send_gpt_load)
    gpt_load_paid_queue_count = len(checkpoint.wait_send_gpt_load_paid)
    gpt_load_rate_limited_queue_count = len(checkpoint.wait_send_gpt_load_rate_limited)
    logger.info(t('queue_status', balancer_queue_count, gpt_load_queue_count))
    if gpt_load_paid_queue_count > 0:
        logger.info(f"💎 付费密钥队列: {gpt_load_paid_queue_count} 个待发送")
    if gpt_load_rate_limited_queue_count > 0:
        logger.info(f"⏰ 429密钥队列: {gpt_load_rate_limited_queue_count} 个待发送")
    
    # 显示异步验证器状态
    logger.info(f"🚀 异步密钥验证器: 已启动，并发数 = {key_validator.max_workers}")

    # 3. 显示系统信息
    search_queries = file_manager.get_search_queries()
    logger.info(t('system_information'))
    logger.info(t('github_tokens_count', len(Config.GITHUB_TOKENS)))
    logger.info(t('search_queries_count', len(search_queries)))
    logger.info(t('date_filter', Config.DATE_RANGE_DAYS))
    if Config.PROXY_LIST:
        logger.info(t('proxy_configured', len(Config.PROXY_LIST)))
    
    # 显示强制冷却配置
    if Config.parse_bool(Config.FORCED_COOLDOWN_ENABLED):
        per_query = f"{Config.FORCED_COOLDOWN_HOURS_PER_QUERY} 小时" if Config.FORCED_COOLDOWN_HOURS_PER_QUERY != "0" else "禁用"
        per_loop = f"{Config.FORCED_COOLDOWN_HOURS_PER_LOOP} 小时" if Config.FORCED_COOLDOWN_HOURS_PER_LOOP != "0" else "禁用"
        logger.info(t('forced_cooldown_status', per_query, per_loop))

    if checkpoint.last_scan_time:
        logger.info(t('checkpoint_found'))
        logger.info(t('last_scan', checkpoint.last_scan_time))
        logger.info(t('scanned_files', len(checkpoint.scanned_shas)))
        logger.info(t('processed_queries', len(checkpoint.processed_queries)))
    else:
        logger.info(t('no_checkpoint'))


    logger.info(t('system_ready'))
    logger.info("=" * 60)

    total_keys_found = 0
    total_rate_limited_keys = 0
    loop_count = 0

    while True:
        try:
            loop_count += 1
            logger.info(t('loop_start', loop_count, datetime.now().strftime('%H:%M:%S')))

            # 清空上一轮的已处理查询，准备新一轮搜索
            if loop_count > 1:
                checkpoint.processed_queries.clear()
                file_manager.save_checkpoint(checkpoint)
                logger.info(t('cleared_queries'))

            query_count = 0
            loop_processed_files = 0
            reset_skip_stats()
            
            # 重置验证器统计（每轮循环开始时）
            key_validator.reset_stats()

            for i, q in enumerate(search_queries, 1):
                normalized_q = normalize_query(q)
                if normalized_q in checkpoint.processed_queries:
                    logger.info(t('skipping_query', q, i))
                    continue

                res = github_utils.search_for_keys(q)
                
                # 标记是否需要冷却（默认需要）
                should_cooldown = True

                # 检查是否是查询语法错误，如果是则跳过（不触发冷却）
                if res and res.get("query_syntax_error"):
                    logger.warning(t('query_syntax_error_skip', q, i, len(search_queries)))
                    checkpoint.add_processed_query(normalized_q)
                    file_manager.save_checkpoint(checkpoint)
                    should_cooldown = False
                    continue

                if res and "items" in res:
                    items = res["items"]
                    if items:
                        query_valid_keys = 0
                        query_rate_limited_keys = 0
                        query_processed = 0

                        for item_index, item in enumerate(items, 1):

                            # 每20个item保存checkpoint并显示进度
                            if item_index % 20 == 0:
                                # 获取当前验证统计
                                validator_stats = key_validator.get_stats()
                                logger.info(t('progress', item_index, len(items), q, validator_stats['valid_keys'], validator_stats['rate_limited_keys'], total_keys_found, total_rate_limited_keys))
                                file_manager.save_checkpoint(checkpoint)
                                file_manager.update_dynamic_filenames()
                                
                                # 定期刷新验证结果
                                valid_count, rate_limited_count, paid_count = key_validator.flush_results()
                                if valid_count > 0 or rate_limited_count > 0:
                                    logger.info(f"💾 刷新验证结果: 有效 {valid_count}, 限速 {rate_limited_count}, 付费 {paid_count}")

                            # 检查是否应该跳过此item
                            should_skip, skip_reason = should_skip_item(item, checkpoint)
                            if should_skip:
                                logger.info(t('skipping_item', item.get('path','').lower(), item_index, skip_reason))
                                continue

                            # 处理单个item（将密钥添加到异步验证队列）
                            keys_found = process_item(item)
                            query_processed += 1

                            # 记录已扫描的SHA
                            sha = item.get("sha")
                            checkpoint.add_scanned_sha(sha)
                            
                            # 如果使用数据库存储，保存SHA到数据库（数据库会自动去重）
                            if Config.STORAGE_TYPE == 'sql':
                                repo_name = item.get("repository", {}).get("full_name", "")
                                file_manager.append_scanned_sha(sha, repo_name)

                            loop_processed_files += 1



                        # 等待当前查询的所有密钥验证完成
                        logger.info(f"⏳ 查询 {i}/{len(search_queries)} 搜索完成，等待密钥验证...")
                        key_validator.wait_completion(timeout=300)  # 最多等待5分钟
                        
                        # 刷新验证结果并获取统计
                        valid_count, rate_limited_count, paid_count = key_validator.flush_results()
                        query_valid_keys = valid_count
                        query_rate_limited_keys = rate_limited_count
                        
                        total_keys_found += query_valid_keys
                        total_rate_limited_keys += query_rate_limited_keys

                        if query_processed > 0:
                            logger.info(t('query_complete', i, len(search_queries), query_processed, query_valid_keys, query_rate_limited_keys))
                            if paid_count > 0:
                                logger.info(f"💎 本次查询发现付费密钥: {paid_count} 个")
                        else:
                            logger.info(t('query_all_skipped', i, len(search_queries)))

                        print_skip_stats()
                    else:
                        # 无搜索结果，跳过冷却
                        should_cooldown = False
                        logger.info(t('query_no_items', i, len(search_queries)))
                        logger.info(f"⏭️  无搜索结果，跳过本次查询的强制冷却")
                else:
                    # 查询失败，跳过冷却
                    should_cooldown = False
                    logger.warning(t('query_failed', i, len(search_queries)))
                    logger.info(f"⏭️  查询失败，跳过本次查询的强制冷却")

                checkpoint.add_processed_query(normalized_q)
                query_count += 1

                checkpoint.update_scan_time()
                file_manager.save_checkpoint(checkpoint)
                file_manager.update_dynamic_filenames()

                # 强制冷却 - 每个查询后（只有在有结果时才冷却）
                if Config.parse_bool(Config.FORCED_COOLDOWN_ENABLED) and should_cooldown:
                    cooldown_hours = Config.parse_cooldown_hours(Config.FORCED_COOLDOWN_HOURS_PER_QUERY)
                    if cooldown_hours > 0:
                        cooldown_seconds = cooldown_hours * 3600  # 保留小数，支持更精确的时间
                        logger.info(t('forced_cooldown_query', cooldown_hours, int(cooldown_seconds)))
                        state.is_in_cooldown = True
                        
                        # 分段休眠，每60秒输出一次剩余时间
                        remaining_seconds = cooldown_seconds
                        interval = 60  # 每60秒更新一次
                        
                        while remaining_seconds > 0:
                            if remaining_seconds <= interval:
                                time.sleep(remaining_seconds)
                                remaining_seconds = 0
                            else:
                                time.sleep(interval)
                                remaining_seconds -= interval
                                remaining_hours = remaining_seconds / 3600
                                remaining_minutes = (remaining_seconds % 3600) / 60
                                logger.info(f"❄️ 冷却中... 剩余时间: {remaining_hours:.2f} 小时 ({int(remaining_minutes)} 分钟 / {remaining_seconds} 秒)")
                        
                        state.is_in_cooldown = False

                if query_count % 5 == 0:
                    logger.info(t('taking_break', query_count))
                    time.sleep(1)

            # 等待本轮所有密钥验证完成
            logger.info(f"⏳ 循环 {loop_count} 搜索完成，等待所有密钥验证完成...")
            key_validator.wait_completion(timeout=600)  # 最多等待10分钟
            
            # 最后一次刷新验证结果
            valid_count, rate_limited_count, paid_count = key_validator.flush_results()
            if valid_count > 0 or rate_limited_count > 0:
                logger.info(f"💾 最终刷新: 有效 {valid_count}, 限速 {rate_limited_count}, 付费 {paid_count}")
            
            logger.info(t('loop_complete', loop_count, loop_processed_files, total_keys_found, total_rate_limited_keys))

            # SHA自动清理 - 每N轮循环后执行一次
            if Config.parse_bool(Config.SHA_CLEANUP_ENABLED) and Config.STORAGE_TYPE == 'sql' and file_manager.db_manager:
                if loop_count % Config.SHA_CLEANUP_INTERVAL_LOOPS == 0:
                    try:
                        logger.info(f"🗑️ 开始清理超过 {Config.SHA_CLEANUP_DAYS} 天的旧SHA记录...")
                        sha_count_before = file_manager.db_manager.get_scanned_shas_count()
                        deleted_count = file_manager.db_manager.clean_old_shas(Config.SHA_CLEANUP_DAYS)
                        sha_count_after = file_manager.db_manager.get_scanned_shas_count()
                        logger.info(f"🗑️ SHA清理完成: 删除 {deleted_count} 条，剩余 {sha_count_after} 条 (之前 {sha_count_before} 条)")
                    except Exception as e:
                        logger.error(f"SHA清理失败: {e}")

            # 强制冷却 - 每轮循环后
            if Config.parse_bool(Config.FORCED_COOLDOWN_ENABLED):
                cooldown_hours = Config.parse_cooldown_hours(Config.FORCED_COOLDOWN_HOURS_PER_LOOP)
                if cooldown_hours > 0:
                    cooldown_seconds = cooldown_hours * 3600  # 保留小数，支持更精确的时间
                    logger.info(t('forced_cooldown_loop', cooldown_hours, int(cooldown_seconds)))
                    state.is_in_cooldown = True
                    
                    # 分段休眠，每60秒输出一次剩余时间
                    remaining_seconds = cooldown_seconds
                    interval = 60  # 每60秒更新一次
                    
                    while remaining_seconds > 0:
                        if remaining_seconds <= interval:
                            time.sleep(remaining_seconds)
                            remaining_seconds = 0
                        else:
                            time.sleep(interval)
                            remaining_seconds -= interval
                            remaining_hours = remaining_seconds / 3600
                            remaining_minutes = (remaining_seconds % 3600) / 60
                            logger.info(f"❄️ 冷却中... 剩余时间: {remaining_hours:.2f} 小时 ({int(remaining_minutes)} 分钟 / {remaining_seconds} 秒)")
                    
                    state.is_in_cooldown = False
                else:
                    logger.info(t('sleeping'))
                    time.sleep(10)
            else:
                logger.info(t('sleeping'))
                time.sleep(10)

        except KeyboardInterrupt:
            logger.info(t('interrupted'))
            
            # 等待验证完成并刷新结果
            logger.info("⏳ 等待剩余密钥验证完成...")
            key_validator.wait_completion(timeout=120)
            key_validator.flush_results()
            
            checkpoint.update_scan_time()
            file_manager.save_checkpoint(checkpoint)
            logger.info(t('final_stats', total_keys_found, total_rate_limited_keys))
            logger.info(t('shutting_down'))
            
            # 关闭验证器和同步工具
            key_validator.shutdown()
            sync_utils.shutdown()
            break
        except Exception as e:
            logger.error(t('unexpected_error', e))
            traceback.print_exc()
            
            # 刷新当前验证结果
            try:
                key_validator.flush_results()
            except:
                pass
            
            logger.info(t('continuing'))
            continue


if __name__ == "__main__":
    main()
