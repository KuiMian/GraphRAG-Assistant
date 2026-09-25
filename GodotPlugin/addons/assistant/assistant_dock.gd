@tool
extends PanelContainer

@onready var response_label: Label = %ResponseLabel
@onready var status_label: Label = %StatusLabel
@onready var code_label: Label = %CodeLabel
@onready var copy_button: Button = %CopyButton
@onready var code_edit: CodeEdit = %CodeEdit
@onready var chat_input: TextEdit = %ChatInput
@onready var submit_button: Button = %SubmitButton
@onready var text_label: RichTextLabel = %TextLabel

#region python backend

const BACKEND_PATH := "../"
const PYTHON_CMD := "uv"
const SERVICE_PATH := BACKEND_PATH + "pipeline/assistant_service.py"

var worker_thread: Thread = null

# 用于线程间安全传递状态与完成事件的信号
signal state_updated(state_text: String)
signal ttfb_received(ttfb_text: String)
signal task_completed(output_buffer: String)

func _start_worker_thread(query: String) -> void:
	if worker_thread != null and worker_thread.is_alive():
		return

	if worker_thread != null:
		worker_thread.wait_to_finish()

	worker_thread = Thread.new()
	worker_thread.start(_thread_execute.bind(query))

## 运行在后台 Worker 线程中，完全不阻塞编辑器主线程
func _thread_execute(query: String) -> void:
	var real_service_path := ProjectSettings.globalize_path("res://" + SERVICE_PATH).simplify_path()
	var backend_root_dir := ProjectSettings.globalize_path("res://" + BACKEND_PATH).simplify_path()

	var lang_val: String = str(config_data.get("language", "zh"))
	var budget_val: String = str(int(config_data.get("thinking_budget", 512)))

	var args: PackedStringArray = [
		"run",
		"--directory", backend_root_dir,
		"python",
		"-u",
		"-X", "utf8",
		real_service_path,
		"--query", query,
		"--lang", lang_val,
		"--budget", budget_val,
		"--json-output"
	]

	var pipe_dict := OS.execute_with_pipe(PYTHON_CMD, args, true)
	if pipe_dict.is_empty() or not pipe_dict.has("stdio"):
		task_completed.emit.call_deferred("")
		return

	var pipe: FileAccess = pipe_dict.get("stdio")
	var pid: int = pipe_dict.get("pid", -1)
	var thread_buffer := ""

	while OS.is_process_running(pid) or not pipe.eof_reached():
		var line := pipe.get_line()
		if not line.is_empty():
			thread_buffer += line + "\n"
			_parse_stream_in_thread(line)
		else:
			OS.delay_msec(20) # 释放 CPU 片刻，避免空转

	pipe.close()
	# 安全切换回主线程处理最终 JSON 渲染
	task_completed.emit.call_deferred(thread_buffer)

func _parse_stream_in_thread(line: String) -> void:
	var clean := line.strip_edges()
	if clean.begins_with(">> State Changed:"):
		var start := clean.find("[")
		var end := clean.find("]")
		if start != -1 and end != -1 and end > start:
			var tag := clean.substr(start + 1, end - start - 1)
			state_updated.emit.call_deferred(tag)
	elif clean.begins_with(">> 收到首字响应") or clean.begins_with(">> First token received"):
		ttfb_received.emit.call_deferred(clean)

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
		return

	var text := file.get_as_text()
	file.close()

	var parsed = JSON.parse_string(text)
	if parsed is Dictionary:
		config_data.merge(parsed, true)
		_refresh_ui()

func save_config() -> void:
	if api_key_input:
		config_data["api_key"] = api_key_input.text.strip_edges()
	if thinking_budget_span:
		config_data["thinking_budget"] = int(thinking_budget_span.value)
	if language_button:
		config_data["language"] = "zh" if language_button.get_selected_id() == 0 else "en"

	var file := FileAccess.open(config_path, FileAccess.WRITE)
	if file:
		file.store_string(JSON.stringify(config_data, "\t"))
		file.close()
		status_label.text = "配置已保存"

#endregion config

#region UI & Event Handlers

func _refresh_ui() -> void:
	if api_key_input:
		api_key_input.text = config_data.get("api_key", "")
	if thinking_budget_span:
		thinking_budget_span.value = config_data.get("thinking_budget", 512)
	if language_button:
		language_button.select(0 if config_data.get("language", "zh") == "zh" else 1)

func _on_submit_button_pressed() -> void:
	var query := chat_input.text.strip_edges()
	if query.is_empty():
		return

	if worker_thread != null and worker_thread.is_alive():
		return

	save_config()

	submit_button.disabled = true
	chat_input.editable = false

	status_label.text = "准备生成..."
	response_label.text = "等待首字推理..."
	code_edit.text = ""
	text_label.text = ""

	_start_worker_thread(query)

func _on_copy_pressed() -> void:
	if code_edit.text.is_empty():
		return
	DisplayServer.clipboard_set(code_edit.text)
	status_label.text = "代码已复制"

# 接收后台线程发射回来的状态信号并实时刷新 UI
func _on_state_updated(tag: String) -> void:
	status_label.text = tag

func _on_ttfb_received(ttfb_str: String) -> void:
	response_label.text = ttfb_str

func _on_task_completed(output_buffer: String) -> void:
	if worker_thread != null:
		worker_thread.wait_to_finish()
		worker_thread = null

	var start_marker := "__GODOT_PLUGIN_PAYLOAD_START__"
	var end_marker := "__GODOT_PLUGIN_PAYLOAD_END__"

	var start_idx := output_buffer.find(start_marker)
	var end_idx := output_buffer.find(end_marker)

	if start_idx != -1 and end_idx != -1:
		var json_str := output_buffer.substr(
			start_idx + start_marker.length(),
			end_idx - (start_idx + start_marker.length())
		).strip_edges()

		var json_parser := JSON.new()
		var err := json_parser.parse(json_str)

		if err == OK and json_parser.data is Dictionary:
			var data: Dictionary = json_parser.data
			if data.get("success", false):
				code_edit.text = data.get("extracted_code", "")

				var clean_desc: String = data.get("response_text", "")
				if clean_desc.is_empty():
					text_label.visible = false
				else:
					text_label.visible = true
					text_label.text = _format_markdown_to_bbcode(clean_desc)

				var target_class: String = data.get("target_class", "Node")
				var is_verified: bool = data.get("is_verified", false)
				var turns: int = int(data.get("correction_attempts", 0))

				code_label.text = "BaseClass: %s | Verified: %s | Correction Rounds: %d" % [
					target_class,
					"通过" if is_verified else "未完全通过",
					turns
				]
				status_label.text = "代码生成完毕"
			else:
				status_label.text = "执行失败: " + data.get("error", "未知错误")
				text_label.text = data.get("error", "")
				text_label.visible = true
		else:
			status_label.text = "JSON 解析失败"
	else:
		status_label.text = "子进程异常退出"

	chat_input.editable = true
	submit_button.disabled = false

#endregion UI & Event Handlers

func _format_markdown_to_bbcode(md_text: String) -> String:
	if md_text.is_empty():
		return ""
	var bbcode := md_text
	var regex_bold := RegEx.new()
	regex_bold.compile("\\*\\*(.*?)\\*\\*")
	bbcode = regex_bold.sub(bbcode, "[b]$1[/b]", true)

	var regex_code := RegEx.new()
	regex_code.compile("`([^`]+)`")
	bbcode = regex_code.sub(bbcode, "[color=#e0a96d]$1[/color]", true)

	var regex_list := RegEx.new()
	regex_list.compile("(?m)^[\\-\\+]\\s+(.*)$")
	bbcode = regex_list.sub(bbcode, " • $1", true)
	return bbcode.strip_edges()

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
	submit_button.pressed.connect(_on_submit_button_pressed)
	copy_button.pressed.connect(_on_copy_pressed)

	state_updated.connect(_on_state_updated)
	ttfb_received.connect(_on_ttfb_received)
	task_completed.connect(_on_task_completed)

	config_path = _get_real_config_path()
	load_config()

	set_process(false)

func _exit_tree() -> void:
	if worker_thread != null and worker_thread.is_alive():
		worker_thread.wait_to_finish()
