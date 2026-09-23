@tool

extends Button

func _enter_tree() -> void:
	pressed.connect(_on_clicked)

func _on_clicked() -> void:
	text = "%d" % randi()
