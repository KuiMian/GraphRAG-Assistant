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

func _get_real_config_path() -> String:
	return ProjectSettings.globalize_path("res://" + CONFIG_PATH).simplify_path()

@onready var api_key_input: LineEdit = %ApiKeyInput
@onready var thinking_budget_span: SpinBox = %ThinkingBudgetSpan
@onready var language_button: OptionButton = %LanguageButton
@onready var save_button: Button = %SaveButton

const BACKEND_PATH := "../"
const CONFIG_PATH := BACKEND_PATH + "config.json"
var config_data := {
	"api_key": "",
	"thinking_budget": 512,
	"language": "zh",
}

var config_path: StringName

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
#endregion


func _ready() -> void:
	config_path = _get_real_config_path()

	response_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	status_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	code_label.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	copy_button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	code_edit.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	chat_input.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	submit_button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED
	save_button.auto_translate_mode = Node.AUTO_TRANSLATE_MODE_DISABLED

	var highlighter := GDScriptSyntaxHighlighter.new()
	code_edit.syntax_highlighter = highlighter

	code_edit.gutters_draw_line_numbers = true
	code_edit.gutters_draw_fold_gutter = true

	save_button.pressed.connect(save_config)

	load_config()
