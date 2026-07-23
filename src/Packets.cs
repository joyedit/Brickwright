using System.Collections.Generic;
using ProtoBuf;

namespace Brickwright
{
    [ProtoContract(ImplicitFields = ImplicitFields.AllPublic)]
    public class BwListRequest { }

    [ProtoContract(ImplicitFields = ImplicitFields.AllPublic)]
    public class BwListResponse
    {
        public List<string> Names = new();
    }

    [ProtoContract(ImplicitFields = ImplicitFields.AllPublic)]
    public class BwPlaceRequest
    {
        public string Name;
        public int Angle;
    }

    [ProtoContract(ImplicitFields = ImplicitFields.AllPublic)]
    public class BwSetRequest { }

    [ProtoContract(ImplicitFields = ImplicitFields.AllPublic)]
    public class BwUndoRequest { }

    [ProtoContract(ImplicitFields = ImplicitFields.AllPublic)]
    public class BwStatus
    {
        public bool Success;
        public string Message;
    }

    [ProtoContract(ImplicitFields = ImplicitFields.AllPublic)]
    public class BwCostRequest
    {
        public string Name;
    }

    [ProtoContract(ImplicitFields = ImplicitFields.AllPublic)]
    public class BwCostResponse
    {
        public string Summary;
    }
}
