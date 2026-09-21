from pipeline.retriever import SymbolicGraphRetriever


def run_benchmarks():
    retriever = SymbolicGraphRetriever("assets/godot4_api_graph.json")

    print("==================================================")
    print("  Case 1: Specialized Motion Query (CharacterBody2D)")
    print("==================================================")
    res1 = retriever.retrieve(
        class_name="CharacterBody2D",
        query="make player move and slide on floor with velocity",
        top_k=4,
    )
    print(retriever.assemble_prompt_context(res1))

    print("\n==================================================")
    print("  Case 2: Irrelevant / Pure Algorithm Query")
    print("==================================================")
    res2 = retriever.retrieve(
        class_name="CharacterBody2D",
        query="implement quick sort algorithm on an array",
        top_k=4,
    )
    print(retriever.assemble_prompt_context(res2))


if __name__ == "__main__":
    run_benchmarks()