@tool

extends PanelContainer

@onready var response_label: Label = %ResponseLabel
@onready var status_label: Label = %StatusLabel
@onready var code_label: Label = %CodeLabel
@onready var copy_button: Button = %CopyButton
@onready var code_edit: CodeEdit = %CodeEdit
@onready var chat_input: TextEdit = %ChatInput
@onready var submit_button: Button = %SubmitButton


#region python backend

const BACKEND_PATH := "../"

const PYTHON_CMD := "uv"
const SERVICE_PATH := BACKEND_PATH + "pipeline/assistant_service.py"

var process_pipe: Dictionary = {}
var pipe_file: FileAccess = null
var process_pid: int = -1
var output_buffer: String = ""

func run_assistant_task(query: String) -> void:
	output_buffer = ""

	var real_service_path := ProjectSettings.globalize_path("res://" + SERVICE_PATH).simplify_path()
	var backend_root_dir := ProjectSettings.globalize_path("res://" + BACKEND_PATH).simplify_path()

	print("工作目录: ", backend_root_dir)
	print("执行脚本: ", real_service_path)

	if not FileAccess.file_exists(real_service_path):
		push_error("错误: 找不到服务文件: " + real_service_path)
		return

	var args: PackedStringArray = [
		"run",
		"--directory", backend_root_dir,
		"python",
		"-X", "utf8",
		real_service_path,
		"--query", query,
		"--budget", "256",
		"--json-output"
	]

	process_pipe = OS.execute_with_pipe(PYTHON_CMD, args, true)

	if process_pipe.is_empty() or not process_pipe.has("stdio"):
		push_error("无法拉起 Python 子进程！请检查命令与 PATH。")
		return

	pipe_file = process_pipe.get("stdio")
	process_pid = process_pipe.get("pid", -1)
	set_process(true)

#endregion python backend

#region config

@onready var api_key_input: LineEdit = %ApiKeyInput
@onready var thinking_budget_span: SpinBox = %ThinkingBudgetSpan
@onready var language_button: OptionButton = %LanguageButton
@onready var save_button: Button = %SaveButton

const CONFIG_PATH := BACKEND_PATH + "config.json"
var config_path: StringName

func _get_real_config_path() -> String:
	return ProjectSettings.globalize_path("res://" + CONFIG_PATH).simplify_path()

var config_data := {
	"api_key": "",
	"thinking_budget": 512,
	"language": "zh",
}

func load_config() -> void:
	var file := FileAccess.open(config_path, FileAccess.READ)
	if not file:
		push_error("Config file not found: " + config_path)
		return

	var text := file.get_as_text()
	file.close()

	var parsed = JSON.parse_string(text)
	if parsed is Dictionary:
		config_data.merge(parsed, true)
		_refresh_ui()
		print("Config file load: ", config_data)
	else:
		push_error("Format of config file might wrong.")

func save_config() -> void:
	if api_key_input:
		config_data["api_key"] = api_key_input.text.strip_edges()
	if thinking_budget_span:
		config_data["thinking_budget"] = int(thinking_budget_span.value)
	if language_button:
		var selected_id = language_button.get_selected_id()
		config_data["language"] = "zh" if selected_id == 0 else "en"

	var file := FileAccess.open(config_path, FileAccess.WRITE)

	var json_str := JSON.stringify(config_data, "\t")
	file.store_string(json_str)
	file.close()
	print("Save config at ", config_path)


#endregion config

func _refresh_ui() -> void:
	if api_key_input:
		api_key_input.text = config_data.get("api_key", "")

	if thinking_budget_span:
		thinking_budget_span.value = config_data.get("thinking_budget", 512)

	if language_button:
		var lang_str: String = config_data.get("language", "zh")
		match lang_str:
			"zh":
				language_button.select(0)
			"en":
				language_button.select(1)

func _disable_auto_translate() -> void:
	response_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	status_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	code_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	copy_button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	code_edit.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	chat_input.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	submit_button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	save_button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED

func _ready() -> void:
	_disable_auto_translate()

	var highlighter := GDScriptSyntaxHighlighter.new()
	code_edit.syntax_highlighter = highlighter

	code_edit.gutters_draw_line_numbers = true
	code_edit.gutters_draw_fold_gutter = true

	save_button.pressed.connect(save_config)
	config_path = _get_real_config_path()
	load_config()

	print("--- 开始测试 Python 管道连接 ---")
	run_assistant_task("当玩家进入此区域时，打印玩家名称并扣除 10 点生命值")

func _process(_delta: float) -> void:
	if process_pid == -1 or pipe_file == null:
		set_process(false)
		return

	while pipe_file.get_error() == OK and not pipe_file.eof_reached():
		var line := pipe_file.get_line()
		if not line.is_empty():
			output_buffer += line + "\n"
			_parse_stream_output(line)
		else:
			break

	if not OS.is_process_running(process_pid):
		while pipe_file.get_error() == OK and not pipe_file.eof_reached():
			var remaining := pipe_file.get_line()
			if not remaining.is_empty():
				output_buffer += remaining + "\n"
				_parse_stream_output(remaining)

		set_process(false)
		pipe_file.close()
		pipe_file = null
		process_pid = -1
		_on_process_completed()

func _parse_stream_output(line: String) -> void:
	var clean := line.strip_edges()
	if clean.begins_with(">> State Changed:"):
		print("[Godot UI 状态捕获] ", clean)
		status_label.text = clean.substr(">> State Changed:".length()).strip_edges()
	elif clean.begins_with(">> 收到首字响应"):
		response_label.text = clean
	elif clean.begins_with("[Notice]") or clean.begins_with("[!]"):
		print("[Python 提示/报错] ", clean)

func _on_process_completed() -> void:
	print("\n--- 进程执行完毕，解析最终结果 ---")

	var start_marker: String = "__GODOT_PLUGIN_PAYLOAD_START__"
	var end_marker: String = "__GODOT_PLUGIN_PAYLOAD_END__"

	var start_idx: int = output_buffer.find(start_marker)
	var end_idx: int = output_buffer.find(end_marker)

	if start_idx != -1 and end_idx != -1:
		var json_str: String = output_buffer.substr(
			start_idx + start_marker.length(),
			end_idx - (start_idx + start_marker.length())
		).strip_edges()

		var json_parser: JSON = JSON.new()
		var err: Error = json_parser.parse(json_str)

		if err == OK:
			var data: Dictionary = json_parser.data
			print("是否验证通过: ", data.get("is_verified", false))
			print("提取基类: ", data.get("target_class", ""))
			print("自愈修正轮数: ", data.get("correction_attempts", 0))
			print("\n生成的 GDScript 代码:\n------------------------------------")
			print(data.get("extracted_code", ""))
			print("------------------------------------")
		else:
			push_error("JSON 解析失败: " + json_parser.get_error_message())
	else:
		status_label.text = "子进程异常退出 (未输出有效 Payload)"
		print("================ [Python 原始输出调试] ================")
		print("缓冲区长度: ", output_buffer.length())
		print("内容:\n", output_buffer if not output_buffer.is_empty() else "(输出为空，大概率是启动命令失败或崩溃在 stderr)")
		print("======================================================")
		response_label.text = output_buffer

		push_error("未能在输出中找到标准 Payload 标记！原始输出:\n" + output_buffer)
